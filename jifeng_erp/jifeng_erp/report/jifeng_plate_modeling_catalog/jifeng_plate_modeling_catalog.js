frappe.query_reports["Jifeng Plate Modeling Catalog"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company", reqd: 1,
            default: frappe.defaults.get_user_default("Company") },
        { fieldname: "search", label: "关键词", fieldtype: "Data" },
    ],
    onload(report) {
        report.page.main.find(".jifeng-modeling-note").remove();
        $("<div class='alert alert-info jifeng-modeling-note'>")
            .text("单价为录入参考值，不计算费用、库存或工资。费用关联仅追溯实际凭证，当前状态不代表已付款。复制勾选仅作记录。")
            .prependTo(report.page.main);
    },
    formatter(value, row, column, data, default_formatter) {
        if (column.fieldname === "image_url" && value) {
            const url = frappe.utils.escape_html(String(value));
            return `<a href="${url}" target="_blank" rel="noopener noreferrer"><img src="${url}" alt="设计原图" style="max-width:100px;max-height:70px;object-fit:contain"></a>`;
        }
        return column.fieldtype === "Data" ? frappe.utils.escape_html(String(value ?? ""))
            : default_formatter(value, row, column, data);
    },
};
