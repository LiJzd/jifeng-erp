import frappe
from frappe.model.document import Document


class JifengEmployeeSupplement(Document):
    def validate(self):
        employee = frappe.get_doc("Employee", self.employee)
        employee.check_permission("read")
        self.employee_name = employee.employee_name
        self.company = employee.company
        self.department = employee.department
        self.identity_number = (self.identity_number or "").strip() or None
        self.bank_account_name = (self.bank_account_name or "").strip() or None

        if self.identity_image:
            if not self.identity_image.startswith("/private/files/"):
                frappe.throw("身份证图片必须是私有附件")
            if not frappe.db.exists("File", {"file_url": self.identity_image, "is_private": 1,
                                             "attached_to_doctype": self.doctype, "attached_to_name": self.name}):
                frappe.throw("身份证图片必须上传到当前档案并保持为私有附件")


def refresh_employee_supplement(employee, method=None):
    """Keep display fields current when the native Employee record changes."""
    name = frappe.db.get_value("Jifeng Employee Supplement", {"employee": employee.name}, "name")
    if not name:
        return
    supplement = frappe.get_doc("Jifeng Employee Supplement", name)
    supplement.employee_name = employee.employee_name
    supplement.company = employee.company
    supplement.department = employee.department
    supplement.save(ignore_permissions=True)
