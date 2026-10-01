frappe.pages["jifeng-piece-workers"].on_page_load = function (wrapper) {
    wrapper.jifeng_piece_workers = new JifengPieceWorkers(wrapper);
};

class JifengPieceWorkers {
    constructor(wrapper) {
        this.page = frappe.ui.make_app_page({ parent: wrapper, title: "工资计件人员", single_column: true });
        this.queryNumber = 0;
        this.root = document.createElement("div");
        this.root.style.padding = "18px";
        this.page.main[0].appendChild(this.root);
        this.root.innerHTML = `<div data-piece-fields style="display:flex;gap:16px;flex-wrap:wrap"></div>
            <p>人员档案的工资方式为计件，才能录入计件工资。请选择公司，按关键词查询。</p>
            <button class="btn btn-primary btn-sm" data-piece-query>查询</button>
            <p data-piece-status></p>
            <div style="overflow:auto"><table class="table table-bordered"><thead><tr>
                <th>员工编号</th><th>姓名</th><th>部门</th><th></th>
            </tr></thead><tbody data-piece-rows></tbody></table></div>`;
        this.controls = {};
        for (const df of [
            { fieldname: "company", fieldtype: "Link", options: "Company", label: "公司", reqd: 1,
                onchange: () => { this.queryNumber += 1; this.render([]); } },
            { fieldname: "keyword", fieldtype: "Data", label: "关键字（姓名或员工编号）" },
        ]) {
            const parent = document.createElement("div");
            parent.style.minWidth = "240px";
            this.root.querySelector("[data-piece-fields]").appendChild(parent);
            this.controls[df.fieldname] = frappe.ui.form.make_control({ parent, df, render_input: true });
        }
        this.controls.company.set_value(frappe.defaults.get_user_default("Company") || "");
        this.root.querySelector("[data-piece-query]").addEventListener("click", () => this.query());
        this.controls.keyword.$input?.on("keydown", event => {
            if (event.key === "Enter") { event.preventDefault(); this.query(); }
        });
    }

    async query() {
        const company = this.controls.company.get_value();
        const keyword = this.controls.keyword.get_value() || "";
        if (!company) { frappe.msgprint("请选择公司"); return; }
        const queryNumber = ++this.queryNumber;
        this.render([]);
        this.root.querySelector("[data-piece-status]").textContent = "正在查询…";
        try {
            const result = await frappe.call({ method: "jifeng_erp.piece_workers.list_piece_workers", args: { company, keyword } });
            if (queryNumber !== this.queryNumber) return;
            const rows = result.message || [];
            this.render(rows, company);
            this.root.querySelector("[data-piece-status]").textContent = rows.length ? `查到 ${rows.length} 位可计件员工` : "没有符合条件的可计件员工";
        } catch (error) {
            if (queryNumber === this.queryNumber) this.root.querySelector("[data-piece-status]").textContent = "查询失败，请核对权限或筛选条件后重试。";
        }
    }

    render(rows, company) {
        const body = this.root.querySelector("[data-piece-rows]");
        body.replaceChildren();
        for (const row of rows) {
            const tr = document.createElement("tr");
            for (const field of ["employee_number", "employee_name", "department"]) {
                const td = document.createElement("td");
                td.textContent = row[field] || "";
                tr.appendChild(td);
            }
            const td = document.createElement("td");
            const button = document.createElement("button");
            button.className = "btn btn-default btn-sm";
            button.textContent = "选择并进入工资录入";
            button.addEventListener("click", () => this.select(company, row.name, button));
            td.appendChild(button);
            tr.appendChild(td);
            body.appendChild(tr);
        }
    }

    async select(company, employee, button) {
        if (this.selecting) return;
        if (window.jifeng_wage_workbench?.is_dirty()) {
            frappe.msgprint("请先保存或核对工资录入中的草稿，再选择其他人员。");
            return;
        }
        this.selecting = true;
        const queryNumber = this.queryNumber;
        button.disabled = true;
        try {
            const result = await frappe.call({ method: "jifeng_erp.piece_workers.select_piece_worker", args: { company, employee } });
            if (!result.message?.eligible || result.message.company !== company || result.message.employee !== employee) {
                throw new Error("人员选择结果不匹配");
            }
            if (!this.root.isConnected || (window.frameElement && !window.frameElement.isConnected)) return;
            if (queryNumber !== this.queryNumber || this.controls.company.get_value() !== company) {
                frappe.msgprint("公司或查询条件已变化，请重新查询后选择。");
                return;
            }
            if (typeof window.jifeng_classic_select_piece_worker === "function") {
                await window.jifeng_classic_select_piece_worker(company, employee);
            } else {
                frappe.route_options = { piece_worker_company: company, piece_worker_employee: employee };
                await frappe.set_route("jifeng-wage-workbench");
            }
        } catch (error) {
            frappe.msgprint("人员资格核对失败，请重新查询后选择。");
        } finally { button.disabled = false; this.selecting = false; }
    }
}
