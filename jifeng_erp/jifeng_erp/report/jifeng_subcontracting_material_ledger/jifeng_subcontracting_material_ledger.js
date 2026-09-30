frappe.query_reports["Jifeng Subcontracting Material Ledger"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company",
            default: frappe.defaults.get_user_default("Company"), reqd: 1 },
        { fieldname: "from_date", label: "记账开始日期", fieldtype: "Date", default: frappe.datetime.month_start(), reqd: 1 },
        { fieldname: "to_date", label: "记账结束日期", fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
        { fieldname: "supplier", label: "委外供应商", fieldtype: "Link", options: "Supplier" },
        { fieldname: "subcontracting_order", label: "委外订单", fieldtype: "Link", options: "Subcontracting Order",
            get_query() { return { filters: { company: frappe.query_report.get_filter_value("company"), docstatus: 1 } }; } },
        { fieldname: "flow_order", label: "流程单", fieldtype: "Link", options: "Jifeng Flow Order",
            get_query() { return { filters: { company: frappe.query_report.get_filter_value("company") } }; } },
        { fieldname: "item_code", label: "来源商品", fieldtype: "Link", options: "Item" },
        { fieldname: "rm_item_code", label: "发退原料", fieldtype: "Link", options: "Item" },
    ],
    onload(report) {
        report.page.main.find(".jifeng-subcontracting-material-note").remove();
        $("<div class='alert alert-info jifeng-subcontracting-material-note'>")
            .text("按发退料凭证记账日期查询，仅列已提交的委外原料发出和退回。数量采用每行原料库存单位，退料计为负的净发出变动；不同原料、单位分别查看，不汇总成品数量。")
            .prependTo(report.page.main);
    },
};
