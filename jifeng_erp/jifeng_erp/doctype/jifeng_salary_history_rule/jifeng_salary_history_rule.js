(() => {
    const columns = {
        basic: "基本工资", time: "计时", piece: "计件", housing: "房补",
        full_attendance: "全勤", meal: "餐费", transport: "车费", night_snack: "夜餐费",
        overtime: "加班费", introduction: "介绍费", assessment: "考核", other_earning: "其他收入",
        utilities: "水电", social_insurance: "社保", fine: "罚款", loan: "借款",
        retained_pay: "留资", accommodation: "住宿费", other_deduction: "其他扣款",
    };
    const display = {
        parentfield: { earnings: "收入", deductions: "员工扣款" },
        bucket: columns,
        classification_mode: { component: "按工资项目", wage_breakdown: "按计件计时来源" },
        extra_bucket: { "": "请选择附加费归入列", ...Object.fromEntries(
            Object.entries(columns).filter(([key]) => !["utilities", "social_insurance", "fine", "loan",
                "retained_pay", "accommodation", "other_deduction"].includes(key))) },
    };
    const apply_labels = (frm) => {
        for (const [field, labels] of Object.entries(display)) {
            // ControlSelect accepts {value,label}; keep native stored values unchanged.
            const options = Object.entries(labels).map(([value, label]) => ({ value, label }));
            const df = frappe.meta.docfield_map[frm.doctype]?.[field];
            // Native readonly Select uses the Data formatter, which supports a
            // formatter on this DocType's field. No global vocabulary changes.
            if (df) df.formatter = (value) => frappe.utils.escape_html(
                value == null || value === "" ? "" : labels[value] || "未识别的配置");
            frm.set_df_property(field, "options", options);
        }
    };
    frappe.ui.form.on("Jifeng Salary History Rule", {
    onload: apply_labels,
    refresh(frm) {
        apply_labels(frm);
        if (frm.is_new() || frm.doc.docstatus !== 0 || !frm.perm?.some(row => row.submit)) return;
        frm.add_custom_button("明确发布首次历史分类", () => {
            frappe.prompt({
                fieldname: "reason", fieldtype: "Small Text", label: "分类依据", reqd: 1,
                description: "用于工资项目首次分类已有历史。发布后保留版本记录，后续修改须追加未来规则。",
            }, ({ reason }) => frm.call("publish_historical", { reason }).then(() => frm.reload_doc()),
            "发布首次历史分类", "发布");
        });
    },
    });
})();
