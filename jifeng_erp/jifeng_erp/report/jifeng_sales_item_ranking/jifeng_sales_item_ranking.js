frappe.query_reports["Jifeng Sales Item Ranking"] = {
    tree: true,
    initial_depth: 0,
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company", reqd: 1,
            default: frappe.defaults.get_user_default("Company") },
        { fieldname: "from_date", label: "凭证记账开始日期", fieldtype: "Date", reqd: 1,
            default: frappe.datetime.month_start() },
        { fieldname: "to_date", label: "凭证记账结束日期", fieldtype: "Date", reqd: 1,
            default: frappe.datetime.get_today() },
        { fieldname: "item_code", label: "商品", fieldtype: "Link", options: "Item" },
    ],
    onload(report) {
        report.page.main.find(".jifeng-sales-item-ranking-note").remove();
        $("<div class='alert alert-info jifeng-sales-item-ranking-note'>")
            .text("净销售商品排行：按本公司可读的已提交销售发票及其记账日期，以商品和来源库存单位分组，按公司本币未税净额从高到低排列，退货保留负数。数量来自发票明细，包含未更新库存的发票，不能视为库存流水。只计销售发票，零售合并发票计一次，尚未合并的原零售凭证不计入。展开可核对精确来源行；汇总值与逐行来源值分列。")
            .prependTo(report.page.main);
    },
    formatter(value, row, column, data, default_formatter) {
        if (column.fieldtype === "Data") {
            return frappe.utils.escape_html(String(value ?? ""));
        }
        return default_formatter(value, row, column, data);
    },
};
