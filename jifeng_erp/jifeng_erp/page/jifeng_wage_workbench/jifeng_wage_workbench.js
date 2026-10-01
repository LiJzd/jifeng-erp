frappe.pages["jifeng-wage-workbench"].on_page_load = function (wrapper) {
    wrapper.jifeng_wages = new JifengWageWorkbench(wrapper);
    window.jifeng_wage_workbench = wrapper.jifeng_wages;
};
frappe.pages["jifeng-wage-workbench"].on_page_show = function (wrapper) {
    wrapper.jifeng_wages?.applyPieceWorker();
};

class JifengWageWorkbench {
    constructor(wrapper) {
        this.page = frappe.ui.make_app_page({ parent: wrapper, title: "工资录入", single_column: true });
        this.rows = [];
        this.requestId = this.requestID();
        this.busy = false;
        this.frozen = false;
        this.storageKey = "jifeng_wage_pending:" + frappe.session.user;
        window.addEventListener("beforeunload", event => {
            if (this.is_dirty()) { event.preventDefault(); event.returnValue = ""; }
        });
        this.root = document.createElement("div");
        this.root.className = "jf-wage-workbench";
        this.page.main[0].appendChild(this.root);
        this.root.innerHTML = `
            <style>
                .jf-wage-workbench{padding:18px}.jf-wage-fields{display:grid;grid-template-columns:repeat(3,minmax(180px,1fr));gap:8px 16px}
                .jf-wage-modes{display:flex;gap:24px;margin:10px 0}.jf-wage-actions{display:flex;gap:8px;margin:14px 0;flex-wrap:wrap}
                .jf-wage-scroll{overflow:auto}.jf-wage-workbench table{width:100%;white-space:nowrap}.jf-wage-summary{padding:14px 0;font-weight:600}
                .jf-wage-workbench [data-wage-result]{padding:12px 0}.jf-wage-hint{color:var(--text-muted);margin:12px 0}
                @media(max-width:850px){.jf-wage-fields{grid-template-columns:repeat(2,minmax(150px,1fr))}}
            </style>
            <div class="jf-wage-fields" data-wage-fields></div>
            <div class="jf-wage-modes">
                <label><input type="checkbox" data-wage-multi> 多人操作</label>
                <label><input type="checkbox" data-wage-multi-operation> 多工序</label>
            </div>
            <p class="jf-wage-hint">双击姓名进入计时；勾选多人操作后双击姓名，填写同一道工序的员工份额；勾选多工序后双击工序，为同一员工逐道录入。计件须核实人员档案工资方式为计件；纯计时继续按现有规则录入。</p>
            <div class="jf-wage-actions">
                <button class="btn btn-default btn-sm" data-wage-add>新增计件</button>
                <button class="btn btn-primary btn-sm" data-wage-save>保存草稿</button>
                <button class="btn btn-default btn-sm" data-wage-check hidden>核对保存结果</button>
                <button class="btn btn-default btn-sm" data-wage-records>工资明细 / 审核</button>
            </div>
            <div class="jf-wage-scroll"><table class="table table-bordered"><thead><tr>
                <th>日期</th><th>姓名</th><th>工序</th><th>工序报工单</th><th>流程单</th><th>数量 / 单位</th><th>工价</th><th>小时</th><th>时薪</th><th>计件金额</th><th>计时金额</th><th>附加费</th><th>合计</th><th></th>
            </tr></thead><tbody data-wage-rows></tbody></table></div>
            <div class="jf-wage-summary" data-wage-summary></div>
            <div data-wage-result></div>`;
        this.controls = {};
        const fields = [
            { fieldname: "work_date", fieldtype: "Date", label: "日期", default: frappe.datetime.get_today(), reqd: 1 },
            { fieldname: "employee", fieldtype: "Link", label: "姓名", options: "Employee", reqd: 1 },
            { fieldname: "operation", fieldtype: "Link", label: "工序", options: "Operation" },
            { fieldname: "company", fieldtype: "Link", label: "公司", options: "Company", reqd: 1 },
            { fieldname: "payroll_date", fieldtype: "Date", label: "计薪日期", default: frappe.datetime.get_today(), reqd: 1 },
            { fieldname: "salary_component", fieldtype: "Link", label: "收入工资项目", options: "Salary Component", reqd: 1 },
        ];
        for (const df of fields) {
            const parent = document.createElement("div");
            parent.dataset.wageField = df.fieldname;
            this.root.querySelector("[data-wage-fields]").appendChild(parent);
            this.controls[df.fieldname] = frappe.ui.form.make_control({ parent, df, render_input: true });
            if (df.default) this.controls[df.fieldname].set_value(df.default);
        }
        this.controls.employee.get_query = () => ({ filters: { company: this.value("company"), status: "Active" } });
        this.controls.salary_component.get_query = () => ({ filters: { type: "Earning", disabled: 0 } });
        this.controls.company.set_value(frappe.defaults.get_user_default("Company") || "");
        this.root.addEventListener("dblclick", event => {
            const field = event.target.closest("[data-wage-field]")?.dataset.wageField;
            if (field === "employee") this.guard(() => this.nameDoubleClick());
            else if (field === "operation" && this.root.querySelector("[data-wage-multi-operation]").checked) this.guard(() => this.entryDialog(false));
        });
        for (const selector of ["[data-wage-multi]", "[data-wage-multi-operation]"]) {
            this.root.querySelector(selector).addEventListener("change", event => {
                if (this.frozen) { event.target.checked = !event.target.checked; return; }
            });
        }
        this.root.querySelector("[data-wage-add]").addEventListener("click", () => this.guard(() => {
            if (this.root.querySelector("[data-wage-multi]").checked) return this.allocationDialog();
            return this.entryDialog(false);
        }));
        this.root.querySelector("[data-wage-save]").addEventListener("click", () => this.guard(() => this.save()));
        this.root.querySelector("[data-wage-check]").addEventListener("click", () => this.guard(() => this.checkResult()));
        this.root.querySelector("[data-wage-records]").addEventListener("click", () => {
            if (this.is_dirty()) { frappe.msgprint("请先保存当前录入或核对保存结果，再查看工资明细。"); return; }
            frappe.set_route("List", "Jifeng Wage Entry", { work_date: frappe.datetime.get_today() });
        });
        this.root.querySelector("[data-wage-rows]").addEventListener("click", event => {
            const button = event.target.closest("[data-remove-wage]");
            if (button && !this.frozen && !this.busy) { this.rows.splice(Number(button.dataset.removeWage), 1); this.renderRows(); }
        });
        this.renderRows();
        this.restorePending();
    }

    value(field) { return this.controls[field].get_value(); }
    requestID() {
        if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
        const bytes = crypto.getRandomValues(new Uint8Array(16));
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
        return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }
    async applyPieceWorker() {
        const company = frappe.route_options?.piece_worker_company;
        const employee = frappe.route_options?.piece_worker_employee;
        if (!company && !employee) return;
        delete frappe.route_options.piece_worker_company;
        delete frappe.route_options.piece_worker_employee;
        return this.choosePieceWorker(company, employee);
    }
    async choosePieceWorker(company, employee) {
        if (this.selectionBusy || this.is_dirty()) { frappe.msgprint("请先保存或核对当前工资草稿，再选择计件人员。"); return false; }
        const previous = { company: this.value("company"), employee: this.value("employee") };
        const locked = ["company", "employee"].map(field => ({
            control: this.controls[field], readOnly: this.controls[field].df.read_only,
        }));
        this.selectionBusy = true;
        for (const { control } of locked) { control.df.read_only = true; control.refresh(); }
        try {
            const result = await frappe.call({ method: "jifeng_erp.piece_workers.select_piece_worker", args: { company, employee } });
            if (!result.message?.eligible || result.message.company !== company || result.message.employee !== employee) throw new Error("人员选择结果不匹配");
            if (this.is_dirty()) { frappe.msgprint("当前已有工资草稿，请先保存或核对。"); return false; }
            if (previous.company !== this.value("company") || previous.employee !== this.value("employee")) {
                frappe.msgprint("公司或姓名已变化，请重新选择计件人员。");
                return false;
            }
            await this.controls.company.set_value(company);
            await this.controls.employee.set_value(employee);
            return true;
        } catch (error) { frappe.msgprint("计件人员资格核对失败，请回到计件人员重新查询。"); return false; }
        finally {
            for (const { control, readOnly } of locked) { control.df.read_only = readOnly; control.refresh(); }
            this.selectionBusy = false;
        }
    }
    is_dirty() { return this.rows.length > 0 || this.frozen || this.busy || Boolean(this.activeDialog && this.activeDialog.is_visible); }
    get_close_block() {
        if (this.selectionBusy) return "正在核对计件人员，请稍后关闭。";
        if (this.busy) return "正在保存，请稍后关闭。";
        if (this.frozen) return "保存结果尚未核实，请先核对保存结果再关闭。";
        return "";
    }
    restorePending() {
        try {
            const pending = JSON.parse(sessionStorage.getItem(this.storageKey) || "null");
            if (!pending) return;
            if (typeof pending.request_id !== "string" || typeof pending.company !== "string" || typeof pending.employee !== "string") throw new Error("invalid pending request");
            this.pending = pending;
            this.requestId = pending.request_id;
            this.controls.company.set_value(pending.company);
            this.controls.employee.set_value(pending.employee);
            this.lock(true);
            this.root.querySelector("[data-wage-result]").textContent = "检测到尚未核实的工资保存请求，请先核对保存结果。";
        } catch (error) {
            this.storageUnavailable = true;
            this.root.querySelector("[data-wage-result]").textContent = "浏览器暂不能保留工资保存请求，请恢复当前浏览器的会话存储后再录入。";
        }
    }
    storePending() {
        if (this.storageUnavailable) throw new Error("浏览器暂不能保留工资保存请求，请恢复会话存储后再录入。");
        const previous = JSON.parse(sessionStorage.getItem(this.storageKey) || "null");
        if (previous && previous.request_id !== this.sentPayload.request_id) throw new Error("当前浏览器已有待核对的工资请求，请先核对原请求。");
        this.pending = { request_id: this.sentPayload.request_id, company: this.sentPayload.company,
            employee: this.sentPayload.employee };
        sessionStorage.setItem(this.storageKey, JSON.stringify(this.pending));
    }
    clearPending() {
        const previous = JSON.parse(sessionStorage.getItem(this.storageKey) || "null");
        if (previous?.request_id === this.pending?.request_id) sessionStorage.removeItem(this.storageKey);
        this.pending = null;
    }
    async guard(action) {
        try {
            if (this.selectionBusy) throw new Error("正在核对计件人员，请稍后操作。");
            await action();
        }
        catch (error) { if (!error?._server_messages) frappe.msgprint(error.message || "工资操作未完成，请核对提示后重试。"); }
    }
    requirePerson() {
        if (!this.value("employee") || !this.value("company")) throw new Error("请先选择公司和姓名。");
        if (!this.value("work_date") || !this.value("payroll_date")) throw new Error("请填写日期和计薪日期。");
    }
    editable() {
        if (this.selectionBusy) throw new Error("正在核对计件人员，请稍后操作。");
        if (this.busy || this.frozen) throw new Error("请先核对当前保存结果，再继续录入。");
        if (this.rows.length && (this.batchPerson.employee !== this.value("employee") || this.batchPerson.company !== this.value("company"))) {
            throw new Error("请先保存当前员工的录入，再更换公司或姓名。");
        }
    }
    nameDoubleClick() {
        this.editable();
        return this.root.querySelector("[data-wage-multi]").checked ? this.allocationDialog() : this.entryDialog(true);
    }
    trackDialog(dialog) {
        this.activeDialog = dialog;
        const initial = JSON.stringify(Object.fromEntries(Object.entries(dialog.fields_dict).filter(([, control]) => control.get_value).map(([field, control]) => [field, control.get_value()])));
        const hide = dialog.hide.bind(dialog);
        dialog.hide = () => {
            const current = JSON.stringify(Object.fromEntries(Object.entries(dialog.fields_dict).filter(([, control]) => control.get_value).map(([field, control]) => [field, control.get_value()])));
            if (!dialog.allowClose && current !== initial) {
                if (dialog.confirming) return;
                dialog.confirming = true;
                frappe.confirm("放弃这次尚未加入明细的录入？", () => queueMicrotask(() => {
                    dialog.confirming = false; dialog.allowClose = true; hide();
                }), () => { dialog.confirming = false; });
                return;
            }
            return hide();
        };
        const previous = dialog.on_hide;
        dialog.on_hide = () => { if (previous) previous(); if (this.activeDialog === dialog) this.activeDialog = null; };
        dialog.show();
        return dialog;
    }
    allocationDialog() {
        this.editable();
        this.requirePerson();
        if (this.rows.length) throw new Error("请先保存当前明细，再打开多人分配。");
        const dialog = new frappe.ui.Dialog({ title: "同工序多人操作", fields: [
            { fieldname: "job_card", fieldtype: "Link", label: "已提交工序报工单", options: "Job Card", reqd: 1,
              get_query: () => ({ filters: { company: this.value("company"), docstatus: 1, is_corrective_job_card: 0,
                  ...(this.value("operation") ? { operation: this.value("operation") } : {}) } }) },
            { fieldname: "salary_component", fieldtype: "Link", label: "收入工资项目", options: "Salary Component", reqd: 1,
              default: this.value("salary_component"), get_query: () => ({ filters: { type: "Earning", disabled: 0 } }) },
            { fieldtype: "HTML", options: "主管逐人填写计件份额。审核时总份额核对这道工序的报工完成量；不自动均分。" },
        ], primary_action_label: "填写员工份额", primary_action: async values => {
            if (dialog.working) return;
            dialog.working = true;
            try {
                await frappe.call({ method: "jifeng_erp.wage_workbench.get_entry_source", args: {
                    job_card: values.job_card, company: this.value("company"), employee: this.value("employee") } });
                dialog.allowClose = true;
                dialog.hide();
                frappe.new_doc("Jifeng Wage Allocation", { job_card: values.job_card,
                    payroll_date: this.value("payroll_date"), salary_component: values.salary_component });
            } catch (error) {
                if (!error?._server_messages) frappe.msgprint(error.message || "来源读取未完成，请重新核对。");
            } finally { dialog.working = false; }
        }});
        this.trackDialog(dialog);
    }
    async entryDialog(timed) {
        this.editable();
        this.requirePerson();
        if (this.rows.length >= 20) throw new Error("每次最多录入二十道工序，请先保存当前明细。");
        const selectedPerson = { employee: this.value("employee"), company: this.value("company") };
        const person = await frappe.db.get_value("Employee", selectedPerson.employee, "employee_name");
        if (selectedPerson.employee !== this.value("employee") || selectedPerson.company !== this.value("company")) return;
        const employeeName = person.message.employee_name || selectedPerson.employee;
        const fields = [
            { fieldname: "work_date", fieldtype: "Date", label: "日期", default: this.value("work_date"), reqd: 1 },
            { fieldname: "operation", fieldtype: "Link", label: "工序", options: "Operation", default: this.value("operation"), reqd: 1 },
            { fieldname: "job_card", fieldtype: "Link", label: "工序报工单", options: "Job Card", reqd: timed ? 0 : 1,
              get_query: () => ({ filters: { company: this.value("company"), docstatus: 1,
                  ...(dialog.get_value("operation") ? { operation: dialog.get_value("operation") } : {}) } }),
              description: timed ? "有报工来源时请明确选择；保存时核对该员工在来源报工单内。" : "明确选择这道工序的已提交报工单。" },
            { fieldname: "source", fieldtype: "HTML" },
            { fieldname: "quantity", fieldtype: "Float", label: "计件数量", default: 0, hidden: timed ? 1 : 0 },
            { fieldname: "piece_rate", fieldtype: "Currency", label: "计件工价", default: 0, hidden: timed ? 1 : 0 },
            { fieldname: "hours", fieldtype: "Float", label: "计时小时", default: 0, reqd: timed ? 1 : 0 },
            { fieldname: "hourly_rate", fieldtype: "Currency", label: "计时单价", default: 0, reqd: timed ? 1 : 0 },
            { fieldname: "extra_amount", fieldtype: "Currency", label: "附加费", default: 0 },
            { fieldname: "notes", fieldtype: "Small Text", label: "备注" },
        ];
        let source = null;
        let sourceRequest = 0;
        const dialog = new frappe.ui.Dialog({ title: timed ? "计时录入" : "工序计件录入", fields,
            primary_action_label: "加入明细", primary_action: async values => {
                if (dialog.working) return;
                dialog.working = true;
                try {
                    if (selectedPerson.employee !== this.value("employee") || selectedPerson.company !== this.value("company")) throw new Error("公司或员工选择已变化，请重新录入。");
                    if (values.job_card && (!source || source.job_card !== values.job_card)) throw new Error("请等待来源读取完成，再核对工序。");
                    if (values.job_card && (source.operation !== values.operation)) throw new Error("工序必须与所选报工单一致。");
                    if (this.rows.some(row => row.job_card && row.job_card === values.job_card)) throw new Error("这道报工单已加入明细，请先核对原行。");
                    if (timed && (!(Number(values.hours) > 0) || !(Number(values.hourly_rate) > 0))) throw new Error("计时录入需要明确填写正数小时和计时单价。");
                    for (const field of ["quantity", "piece_rate", "hours", "hourly_rate", "extra_amount"]) {
                        if (!Number.isFinite(Number(values[field])) || Number(values[field]) < 0) throw new Error("数量、单价、小时和附加费需要填写非负数。");
                    }
                    if (!this.rows.length) this.batchPerson = { ...selectedPerson, employee_name: employeeName };
                    this.rows.push({ work_date: values.work_date, operation: values.operation, job_card: values.job_card || "",
                        source_modified: source?.source_modified || "", work_order_modified: source?.work_order_modified || "",
                        stock_uom: source?.stock_uom || "", quantity: timed ? 0 : Number(values.quantity || 0),
                        piece_rate: timed ? 0 : Number(values.piece_rate || 0), hours: Number(values.hours || 0),
                        hourly_rate: Number(values.hourly_rate || 0), extra_amount: Number(values.extra_amount || 0),
                        notes: values.notes || "", flow_order: source?.flow_order || "" });
                    dialog.allowClose = true;
                    dialog.hide();
                    this.renderRows();
                } catch (error) { frappe.msgprint(error.message); }
                finally { dialog.working = false; }
            }});
        dialog.fields_dict.job_card.df.onchange = async () => {
            const selected = dialog.get_value("job_card");
            const request = ++sourceRequest;
            source = null;
            dialog.fields_dict.source.$wrapper.empty();
            if (!selected) return;
            try {
                const response = await frappe.call({ method: "jifeng_erp.wage_workbench.get_entry_source", args: {
                    job_card: selected, company: this.value("company"), employee: this.value("employee") } });
                if (request !== sourceRequest || dialog.get_value("job_card") !== selected) return;
                source = response.message;
                dialog.set_value("operation", source.operation);
                dialog.fields_dict.source.$wrapper.html(`<p>来源日期：${this.esc(source.source_date)}　单位：${this.esc(source.stock_uom)}　流程单：${this.esc(source.flow_order || "")}</p>`);
            } catch (error) { if (request === sourceRequest) source = null; }
        };
        this.trackDialog(dialog);
    }
    esc(value) { return frappe.utils.escape_html(String(value ?? "")); }
    money(value) { return (Math.round((Number(value) + Number.EPSILON) * 100) / 100).toFixed(2); }
    renderRows() {
        let piece = 0, time = 0, total = 0;
        const quantities = new Map();
        this.root.querySelector("[data-wage-rows]").innerHTML = this.rows.map((row, index) => {
            const p = Number(this.money(row.quantity * row.piece_rate));
            const t = Number(this.money(row.hours * row.hourly_rate));
            const sum = Number(this.money(p + t + row.extra_amount));
            piece += p; time += t; total += sum;
            if (row.quantity) quantities.set(row.stock_uom, (quantities.get(row.stock_uom) || 0) + row.quantity);
            const cells = [row.work_date, this.batchPerson?.employee_name || this.batchPerson?.employee || "", row.operation, row.job_card, row.flow_order,
                `${row.quantity} ${row.stock_uom}`, row.piece_rate, row.hours, row.hourly_rate,
                this.money(p), this.money(t), row.extra_amount, this.money(sum)];
            return "<tr>" + cells.map(value => `<td>${this.esc(value)}</td>`).join("") +
                `<td><button class="btn btn-default btn-xs" data-remove-wage="${index}" ${this.frozen ? "disabled" : ""}>删除</button></td></tr>`;
        }).join("");
        const qty = [...quantities].map(([unit, quantity]) => `${quantity} ${this.esc(unit)}`).join("；") || "0";
        this.root.querySelector("[data-wage-summary]").innerHTML = `数量：${qty}　计件金额：${this.money(piece)}　计时金额：${this.money(time)}　合计：${this.money(total)}`;
    }
    lock(frozen) {
        this.frozen = frozen;
        for (const control of Object.values(this.controls)) { control.df.read_only = frozen ? 1 : 0; control.refresh(); }
        for (const selector of ["[data-wage-multi]", "[data-wage-multi-operation]", "[data-wage-add]"]) this.root.querySelector(selector).disabled = frozen;
        this.root.querySelector("[data-wage-check]").hidden = !frozen;
        this.renderRows();
    }
    payload() {
        return { request_id: this.requestId, company: this.batchPerson.company, employee: this.batchPerson.employee,
            payroll_date: this.value("payroll_date"), salary_component: this.value("salary_component"),
            rows: this.rows.map(({ flow_order, ...row }) => row) };
    }
    async save() {
        if (this.selectionBusy) throw new Error("正在核对计件人员，请稍后保存。");
        if (this.busy) return;
        if (this.frozen) return this.checkResult();
        if (!this.rows.length) throw new Error("请先双击姓名或工序，加入工资明细。");
        this.editable();
        if (!this.value("salary_component")) throw new Error("请选择收入工资项目。");
        this.sentPayload = this.payload();
        this.storePending();
        this.lock(true);
        this.busy = true;
        try {
            const response = await frappe.call({ method: "jifeng_erp.wage_workbench.save_drafts", args: { payload: this.sentPayload } });
            this.saved(response.message.wages);
        } catch (error) {
            await this.checkResult();
        } finally { this.busy = false; }
    }
    async checkResult() {
        if (!this.frozen || !this.pending || this.checking) return;
        this.checking = true;
        try {
            const response = await frappe.call({ method: "jifeng_erp.wage_workbench.batch_status", args: {
                request_id: this.pending.request_id, company: this.pending.company, employee: this.pending.employee } });
            const wages = response.message.wages;
            if (wages.length && (!this.sentPayload || wages.length === this.sentPayload.rows.length)) this.saved(wages);
            else if (!wages.length) {
                this.clearPending();
                this.lock(false);
                this.requestId = this.requestID();
                this.sentPayload = null;
                frappe.msgprint("已核对本次请求未保存，明细保留。请修正提示中的问题后重新保存。");
            } else frappe.msgprint("本次请求结果需要核对，请保留当前页面和明细。");
        } catch (error) { frappe.msgprint("保存结果尚未核实，请保留当前页面并再次核对保存结果。"); }
        finally { this.checking = false; }
    }
    saved(wages) {
        this.root.querySelector("[data-wage-result]").innerHTML = "已保存计薪记录草稿：" + wages.map(row =>
            `<a href="${frappe.utils.get_form_link("Jifeng Wage Entry", row.name)}">${this.esc(row.name)}${row.docstatus === 2 ? "（已取消）" : ""}</a>`).join("、") + "。核对后按原生工资流程审核。";
        this.rows = [];
        this.sentPayload = null;
        this.clearPending();
        this.requestId = this.requestID();
        this.lock(false);
    }
}
