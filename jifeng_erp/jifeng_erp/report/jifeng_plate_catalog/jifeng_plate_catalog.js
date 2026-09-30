frappe.query_reports["Jifeng Plate Catalog"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company", reqd: 1,
            default: frappe.defaults.get_user_default("Company") },
        { fieldname: "item_code", label: "关联商品", fieldtype: "Link", options: "Item" },
        { fieldname: "status", label: "档案状态", fieldtype: "Select", options: "\n在用\n停用" },
    ],
    onload(report) {
        report.page.main.find(".jifeng-catalog-note").remove();
        $("<div class='alert alert-info jifeng-catalog-note'>")
            .text("显示本公司可查看的服务器档案，可筛选在用及历史停用记录。金额是档案参考值，不计入实际库存或费用；材料用量按各行单位展示，档案重量沿用原记录。")
            .prependTo(report.page.main);
    },
    formatter(value, row, column, data, default_formatter) {
        if (column.fieldname === "image_url" && value) {
            const url = frappe.utils.escape_html(String(value));
            return `<a href="${url}" target="_blank" rel="noopener noreferrer"><img src="${url}" alt="档案图片" style="max-width:100px;max-height:70px;object-fit:contain"></a>`;
        }
        return default_formatter(value, row, column, data);
    },
};
