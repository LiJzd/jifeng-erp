import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from jifeng_erp.payroll import active_additional_salary, wage_amounts


class JifengWageEntry(Document):
    def validate(self):
        employee = frappe.get_doc("Employee", self.employee)
        if employee.company != self.company or employee.status != "Active":
            frappe.throw("计薪员工必须是当前公司的在职员工")
        if self.flow_order:
            flow = frappe.get_doc("Jifeng Flow Order", self.flow_order)
            if flow.company != self.company:
                frappe.throw("流程单公司与计薪公司不一致")
        if self.work_order:
            work = frappe.get_doc("Work Order", self.work_order)
            if work.company != self.company:
                frappe.throw("生产工单公司与计薪公司不一致")
            if self.flow_order and work.jf_flow_order != self.flow_order:
                frappe.throw("生产工单与流程单不一致")
        if self.salary_component:
            component = frappe.get_doc("Salary Component", self.salary_component)
            if component.type != "Earning" or component.disabled:
                frappe.throw("请选择启用的收入类工资项目")
        piece, timed, total = wage_amounts(
            self.quantity, self.piece_rate, self.hours, self.hourly_rate, self.extra_amount,
        )
        if not (piece or timed or total):
            frappe.throw("计薪金额必须大于零")
        if self.work_date and self.payroll_date and getdate(self.payroll_date) < getdate(self.work_date):
            frappe.throw("计薪日期不能早于工作日期")
        self.piece_amount, self.time_amount, self.total_amount = piece, timed, total
        self.employee_name = employee.employee_name

    def on_cancel(self):
        if active_additional_salary(self.name):
            frappe.throw("请先取消或删除关联的工资附加项，再取消计薪记录")

    @frappe.whitelist()
    def make_additional_salary(self):
        self.check_permission("read")
        frappe.db.get_value(self.doctype, self.name, "name", for_update=True)
        self.reload()
        if self.docstatus != 1:
            frappe.throw("请先审核计薪记录")
        existing = active_additional_salary(self.name)
        if existing:
            frappe.get_doc("Additional Salary", existing).check_permission("read")
            return existing

        assignment = frappe.db.get_value(
            "Salary Structure Assignment",
            {"employee": self.employee, "company": self.company, "docstatus": 1,
             "from_date": ["<=", self.payroll_date]},
            "currency", order_by="from_date desc",
        )
        if not assignment:
            frappe.throw("员工在计薪日期前需要已提交的工资结构分配")
        company_currency = frappe.db.get_value("Company", self.company, "default_currency")
        if assignment != company_currency:
            frappe.throw("员工工资结构币种与计薪公司的默认币种不一致，暂不能直接生成工资附加项")
        salary = frappe.get_doc({
            "doctype": "Additional Salary", "naming_series": "HR-ADS-.YY.-.MM.-",
            "employee": self.employee,
            "company": self.company, "salary_component": self.salary_component,
            "currency": assignment, "amount": self.total_amount,
            "payroll_date": self.payroll_date, "is_recurring": 0,
            "overwrite_salary_structure_amount": 0,
            "ref_doctype": self.doctype, "ref_docname": self.name,
        })
        salary.insert()
        return salary.name
