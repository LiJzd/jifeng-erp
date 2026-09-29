import frappe
from frappe.model.document import Document

from jifeng_erp.accounting import validate_expense_voucher


class JifengExpenseVoucher(Document):
    def validate(self):
        validate_expense_voucher(self)
        if self.docstatus == 0 and self.journal_entry:
            frappe.throw("草稿费用登记不能预先关联总账凭证")

    def on_submit(self):
        if frappe.db.exists("Journal Entry", {"jf_expense_voucher": self.name}):
            frappe.throw("该费用登记已有总账凭证，请先核对")
        amount = validate_expense_voucher(self)
        journal = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Journal Entry",
            "company": self.company,
            "posting_date": self.posting_date,
            "user_remark": self.summary,
            "jf_expense_voucher": self.name,
            "accounts": [
                {"account": self.expense_account, "cost_center": self.cost_center,
                 "debit_in_account_currency": amount},
                {"account": self.payment_account, "credit_in_account_currency": amount},
            ],
        })
        journal.flags.from_jifeng_expense = True
        journal.insert()
        journal.submit()
        self.db_set("journal_entry", journal.name, update_modified=False)

    def on_cancel(self):
        if not self.journal_entry:
            frappe.throw("费用登记缺少关联总账凭证，请先核对")
        journal = frappe.get_doc("Journal Entry", self.journal_entry)
        journal.check_permission("cancel")
        if journal.get("jf_expense_voucher") != self.name:
            frappe.throw("关联总账凭证的来源费用登记不一致")
        if journal.docstatus != 1:
            frappe.throw("关联总账凭证不是已提交状态，请先核对")
        journal.flags.from_jifeng_expense = True
        journal.cancel()
