frappe.query_reports["Jifeng Stock Entry Ledger"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company",
            default: frappe.defaults.get_user_default("Company"), reqd: 1 },
        { fieldname: "from_date", label: "记账开始日期", fieldtype: "Date", default: frappe.datetime.month_start(), reqd: 1 },
        { fieldname: "to_date", label: "记账结束日期", fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
        { fieldname: "mode", label: "业务类型", fieldtype: "Select", options: [
            { value: "receipt", label: "库存收货" }, { value: "issue", label: "物料出库" },
            { value: "transfer", label: "仓库调拨" }], default: "issue", reqd: 1 },
        { fieldname: "item_code", label: "商品/原料", fieldtype: "Link", options: "Item" },
        { fieldname: "warehouse", label: "仓库（含下级）", fieldtype: "Link", options: "Warehouse",
            get_query() { return { filters: { company: frappe.query_report.get_filter_value("company") } }; } },
        { fieldname: "flow_order", label: "流程单", fieldtype: "Link", options: "Jifeng Flow Order",
            get_query() { return { filters: { company: frappe.query_report.get_filter_value("company") } }; } },
    ],
    onload(report) {
        report.page.main.find(".jifeng-stock-entry-note").remove();
        $("<div class='alert alert-info jifeng-stock-entry-note'>")
            .text("按库存凭证记账日期查询，每条明细只列一次，库存数量与正式台账核对。出库显示负净变动，调拨两仓分别列出且公司数量净变动为零；在途两阶段分别保留来源。委外原料退料请查专用原料发退台账。库存价值来自台账，单位和币种分列，不同单位不合计；非库存条目留空库存数量。")
            .prependTo(report.page.main);
    },
};
