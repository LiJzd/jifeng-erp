frappe.query_reports["Jifeng Product Analysis"] = {
    tree: true,
    initial_depth: 0,
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company", reqd: 1,
            default: frappe.defaults.get_user_default("Company") },
        { fieldname: "dimension", label: "分析维度", fieldtype: "Select", reqd: 1,
            options: "品牌\n类别\n材质\n来源\n设计\n打版\n建模\n风格", default: "品牌" },
        { fieldname: "item_code", label: "关联商品", fieldtype: "Link", options: "Item" },
        { fieldname: "status", label: "档案状态", fieldtype: "Select", options: "\n在用\n停用" },
    ],
    onload(report) {
        report.page.main.find(".jifeng-product-analysis-note").remove();
        $("<div class='alert alert-info jifeng-product-analysis-note'>")
            .text("按本公司可读产品档案分组计数，展开分组可查看来源档案。类别分别保留大类、小类；未填写项独立分组。设计、打版和建模按档案原文字分类。")
            .prependTo(report.page.main);
    },
    formatter(value, row, column, data, default_formatter) {
        if (column.fieldtype === "Data") {
            return frappe.utils.escape_html(String(value ?? ""));
        }
        return default_formatter(value, row, column, data);
    },
};
