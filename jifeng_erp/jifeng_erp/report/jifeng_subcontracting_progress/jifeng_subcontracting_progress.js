frappe.query_reports["Jifeng Subcontracting Progress"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company", reqd: 1,
            default: frappe.defaults.get_user_default("Company") },
        { fieldname: "from_date", label: "订单开始日期", fieldtype: "Date", default: frappe.datetime.month_start(), reqd: 1 },
        { fieldname: "to_date", label: "订单结束日期", fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
        { fieldname: "supplier", label: "委外供应商", fieldtype: "Link", options: "Supplier" },
        { fieldname: "subcontracting_order", label: "委外订单", fieldtype: "Link", options: "Subcontracting Order",
            get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company"), docstatus: 1 } }) },
        { fieldname: "item_code", label: "商品", fieldtype: "Link", options: "Item" },
        { fieldname: "flow_order", label: "流程单", fieldtype: "Link", options: "Jifeng Flow Order",
            get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company") } }) },
        { fieldname: "only_pending", label: "仅待收回", fieldtype: "Check" },
        { fieldname: "only_overdue", label: "仅逾期待收回", fieldtype: "Check" },
    ],
    onload(report) {
        report.page.main.find(".jifeng-subcontracting-note").remove();
        $("<div class='alert alert-info jifeng-subcontracting-note'>").text("按订单日期选批次，纳入这些订单全部已提交收回和实物退回。已覆盖数量＝净接收＋净拒收＋确认损耗；待收回数量扣除已覆盖量，已关闭订单不进入待收回和逾期。拒收品仍需品检处理，不能当作合格入库。全部数量按各明细换算为库存单位，不同单位不合计。本表查询当前公司全部委外业务，可用供应商、商品或流程单筛选。副产品和联产品不增加主订单收回量。")
            .prependTo(report.page.main);
    },
}; 
