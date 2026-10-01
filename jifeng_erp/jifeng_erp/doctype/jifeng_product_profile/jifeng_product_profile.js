frappe.ui.form.on("Jifeng Product Profile", {
    refresh(frm) {
        const image = frm.fields_dict.profile_image;
        if (image) {
            image.df.options = {
                doctype: frm.doctype,
                docname: frm.doc.name,
                fieldname: "profile_image",
                make_attachments_public: 0,
                allow_toggle_private: false,
                allow_web_link: false,
                allow_google_drive: false,
            };
            const canWrite = (frm.perm || []).some((permission) =>
                Number(permission.permlevel || 0) === 0 && permission.write);
            frm.set_df_property("profile_image", "read_only", frm.is_new() || !canWrite);
        }
        if (!frm.is_new() && frm.doc.bom) {
            frm.add_custom_button("从BOM更新物料与工序", async () => {
                if (frm.is_dirty()) await frm.save();
                await frm.call("load_bom");
                await frm.reload_doc();
            });
        }
    }
});
