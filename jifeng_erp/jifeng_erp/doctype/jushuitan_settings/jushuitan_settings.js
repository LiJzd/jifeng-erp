frappe.ui.form.on("Jushuitan Settings", {
    refresh(frm) {
        const call = async (method) => {
            if (frm.is_dirty()) await frm.save();
            const r = await frappe.call({ method: "jifeng_erp.sync." + method, freeze: true });
            frappe.msgprint(typeof r.message === "string" ? r.message : r.message.message);
            await frm.reload_doc();
        };
        frm.add_custom_button("测试订单连接", () => call("test_connection"));
        frm.add_custom_button("立即同步", () => call("enqueue_sync"));
        frm.add_custom_button("查看同步记录", () => frappe.set_route("List", "Jifeng Sync Run"));
        if (frm.doc.app_type === "自有商城") {
            frm.add_custom_button("刷新商家授权", () => call("refresh_authorization"));
        }
    }
});
