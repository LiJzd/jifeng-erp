frappe.ui.form.on("Jushuitan Settings", {
    refresh(frm) {
        const call = async (method) => {
            if (frm.is_dirty()) await frm.save();
            let r;
            try {
                r = await frappe.call({ method: "jifeng_erp.sync." + method, freeze: true });
            } catch (error) {
                if (["refresh_authorization", "acquire_initial_authorization"].includes(method)) {
                    frappe.msgprint({
                        title: "授权结果未确认",
                        message: "系统不会自动重复授权请求。请先核实聚水潭开放平台的授权状态，再决定是否手动重试。",
                        indicator: "orange",
                    });
                }
                return;
            }
            frappe.msgprint(typeof r.message === "string" ? r.message : r.message.message);
            await frm.reload_doc();
        };
        frm.add_custom_button("测试订单连接", () => call("test_connection"));
        frm.add_custom_button("立即同步", () => call("enqueue_sync"));
        frm.add_custom_button("查看同步记录", () => frappe.set_route("List", "Jifeng Sync Run"));
        if (frm.doc.app_type === "自有商城") {
            frm.add_custom_button("获取初始授权", () => frappe.confirm(
                "仅在尚无有效商家令牌或需要重新授权时使用。请求结果未知时请先核实开放平台状态，系统不会自动重试。",
                () => call("acquire_initial_authorization")
            ));
            frm.add_custom_button("刷新商家授权", () => frappe.confirm(
                "仅在令牌仍有效时刷新。若结果未知，请先核实开放平台授权状态，不要立即重复请求。",
                () => call("refresh_authorization")
            ));
        }
    }
});
