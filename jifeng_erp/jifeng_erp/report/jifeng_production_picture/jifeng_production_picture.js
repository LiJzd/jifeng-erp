frappe.query_reports["Jifeng Production Picture"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company",
            default: frappe.defaults.get_user_default("Company"), reqd: 1 },
        { fieldname: "from_date", label: "工单建立开始日期", fieldtype: "Date", default: frappe.datetime.month_start(), reqd: 1 },
        { fieldname: "to_date", label: "工单建立结束日期", fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
        { fieldname: "flow_order", label: "流程单", fieldtype: "Link", options: "Jifeng Flow Order" },
        { fieldname: "sales_order", label: "销售订单", fieldtype: "Link", options: "Sales Order" },
        { fieldname: "production_item", label: "商品", fieldtype: "Link", options: "Item" },
        { fieldname: "status", label: "工单状态", fieldtype: "Select",
            options: [
                { value: "", label: "全部" }, { value: "Draft", label: "草稿" },
                { value: "Not Started", label: "未投产" }, { value: "In Process", label: "生产中" },
                { value: "Stock Reserved", label: "库存已预留" }, { value: "Completed", label: "已完成" },
                { value: "Stopped", label: "已停止" }, { value: "Closed", label: "已关闭" },
            ] },
        { fieldname: "only_overdue", label: "仅逾期未完工", fieldtype: "Check" },
    ],
    formatter(value, row, column, data, default_formatter) {
        if (column.fieldname !== "image_url") {
            return default_formatter(value, row, column, data);
        }
        if (!value) {
            return $("<span>").text(data.image_status || "未上传")[0].outerHTML;
        }
        const picture = $("<img>", {
            src: value, alt: data.item_name || data.production_item || "商品图片",
            loading: "lazy", referrerpolicy: "no-referrer",
        }).css({ width: "105px", height: "70px", "object-fit": "contain" });
        return $("<a>", { href: value, target: "_blank", rel: "noopener noreferrer" })
            .append(picture)[0].outerHTML;
    },
    get_datatable_options(options) {
        return { ...options, cellHeight: 82 };
    },
    onload(report) {
        report.page.main.find(".jifeng-production-note").remove();
        $("<div class='alert alert-info jifeng-production-note'>")
            .text("按工单建立日期查看商品图片和正式生产计划；完工和损耗以制造入库凭证为准。图片优先取可读流程单，其次取可读商品档案；私有附件仍受原有权限控制，外部图片由浏览器直接加载。数量按各行单位查看。")
            .prependTo(report.page.main);
    },
};
