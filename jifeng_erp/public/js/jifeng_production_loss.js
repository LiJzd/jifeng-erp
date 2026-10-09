/* A native Stock Entry draft requires an explicit later submit. */
(() => {
  const api = "jifeng_erp.production_loss.";
  const keyFor = (frm) => `jf-loss:${frappe.session.user}:${frm.doc.name}`;
  const call = async (method, args, type = "GET") =>
    (await frappe.call({method: api + method, args, type, freeze: true})).message;
  function request(frm) {
    const key = keyFor(frm);
    let value = localStorage.getItem(key);
    if (!value) {
      value = crypto.randomUUID();
      localStorage.setItem(key, value);
      if (localStorage.getItem(key) !== value) throw new Error("办理标记保存失败，请核对浏览器本地存储");
    }
    return value;
  }
  function clearRequest(frm, id) {
    if (localStorage.getItem(keyFor(frm)) !== id) return false;
    localStorage.removeItem(keyFor(frm));
    return true;
  }
  function openResult(frm, result, id) {
    if (result.docstatus === 2) {
      clearRequest(frm, id);
      frappe.msgprint("原损耗出库已取消。请刷新并重新核对报工后，另行办理。不会自动重新生成。");
    }
    frappe.set_route("Form", "Stock Entry", result.stock_entry);
  }
  frappe.ui.form.on("Job Card", {
    refresh(frm) {
      const previousRequest = localStorage.getItem(keyFor(frm));
      const eligible = !(frm.doc.docstatus !== 1 || frm.doc.is_subcontracted || frm.doc.track_semi_finished_goods ||
          frm.doc.is_corrective_job_card || Number(frm.doc.total_completed_qty) !== 0 ||
          Number(frm.doc.pending_qty) !== 0 || !(Number(frm.doc.process_loss_qty) > 0));
      if (previousRequest) {
        // This recovery action neither generates a UUID nor opens a new-create
        // dialog. It stays available if the original Card has since changed.
        frm.add_custom_button("回读损耗办理结果", async () => {
          const name = frm.doc.name;
          let result;
          try {
            result = await call("inspect_loss_issue", {job_card: name, request_id: previousRequest, only_previous: 1});
          } catch (error) {
            return frappe.msgprint("未能查明原办理结果，旧标记已保留。请恢复权限或连接后再次回读，不要重复提交。");
          }
          if (window.cur_frm !== frm || frm.doc.name !== name) return;
          if (result.existing && result.docstatus !== 2) return openResult(frm, result, previousRequest);
          // A confirmed cancellation/absence releases only this exact old
          // marker. Reload is a read, and never opens a new-create dialog.
          const cleared = clearRequest(frm, previousRequest);
          const status = result.existing ? "原损耗出库已取消。" : "已确认原办理标记没有对应损耗单。";
          frappe.msgprint(status + (cleared ? "旧办理标记已清除。" : "当前办理标记已变化，已保留当前标记。") +
            "请重新核对当前报工后明确办理。");
          try {
            if (!frm.is_dirty()) await frm.reload_doc();
            else frm.refresh();
          } catch (error) {
            frappe.msgprint("原办理结果已核实，" + (cleared ? "旧标记已清除。" : "当前标记已保留。") +
              "页面刷新失败，请手工刷新并核对当前报工后再办理。");
          }
        }, "生产办理");
        return;
      }
      if (!eligible) return;
      frm.add_custom_button("全部损耗出库", async () => {
        const name = frm.doc.name, version = frm.doc.modified;
        const current = () => window.cur_frm === frm && frm.doc.name === name &&
          frm.doc.modified === version && !frm.is_dirty();
        let id;
        try {
          id = request(frm);
          const source = await call("inspect_loss_issue", {job_card: name, request_id: id});
          if (!current()) return frappe.msgprint("报工页面已变化，请刷新后重新核对损耗");
          if (source.existing) return openResult(frm, source, id);
          let confirming = false;
          const dialog = new frappe.ui.Dialog({title: "确认全部损耗并生成出库草稿", fields: [
            {fieldtype: "HTML", options: `<p>实际损耗：${frappe.utils.escape_html(String(source.loss_qty))} ` +
              `${frappe.utils.escape_html(source.stock_uom)}。将从原工单在制仓库出库；确认没有可回收实物及残值后办理。</p>`},
            {fieldname: "warehouse", fieldtype: "Link", options: "Warehouse", label: "原工单在制仓库",
              default: source.warehouse, read_only: 1, reqd: 1},
            {fieldname: "reason", fieldtype: "Small Text", label: "实际损耗及报废原因", reqd: 1},
            {fieldname: "no_residual", fieldtype: "Check", label: "确认无可回收实物及残值", reqd: 1}
          ], primary_action_label: "生成损耗出库草稿", async primary_action(values) {
            if (confirming) return;
            if (!current()) return frappe.msgprint("报工页面已变化，请刷新后重新核对损耗");
            if (Number(values.no_residual) !== 1) return frappe.msgprint("请明确确认没有可回收实物及残值");
            confirming = true;
            dialog.get_primary_btn().prop("disabled", true);
            try {
              const result = await call("create_loss_issue", {job_card: name, request_id: id,
                expected_version: source.source_version, warehouse: values.warehouse,
                no_residual: values.no_residual, reason: values.reason}, "POST");
              dialog.hide();
              if (!current()) return frappe.msgprint("损耗办理结果已返回。当前页面已切换，请从原报工单回读损耗单。");
              openResult(frm, result, id);
            } catch (error) {
              // Never repeat an unknown write. A fresh GET resolves this exact
              // request before any later human action may issue another POST.
              dialog.hide();
              try {
                const result = await call("inspect_loss_issue", {job_card: name, request_id: id});
                if (!current()) return frappe.msgprint("已回读办理结果。当前页面已切换，请从原报工单查看损耗单。");
                if (result.existing) return openResult(frm, result, id);
                frappe.msgprint("本次未找到对应损耗单。请核对服务器提示并刷新报工，再明确重新办理。");
              } catch (readError) {
                frappe.msgprint("未能查明本次办理结果，请勿重复提交。恢复连接后点击‘全部损耗出库’先回读原办理标记。");
              }
            }
          }});
          dialog.show();
        } catch (error) {
          frappe.msgprint(error.message || "暂时无法完整核对损耗来源，请刷新后查看服务器提示");
        }
      }, "生产办理");
    }
  });
})();
