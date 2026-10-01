import frappe
from frappe.model.document import Document

from jifeng_erp.accounting import (
    CREDIT, expense_journal, expense_journal_action, payment_context,
    validate_credit_cancellation, validate_expense_voucher,
)


class JifengExpenseVoucher(Document):
    def validate(self):
        validate_expense_voucher(self)
        if self.docstatus == 0 and self.journal_entry:
            frappe.throw("草稿费用登记不能预先关联总账凭证")

    def on_submit(self):
        if frappe.db.exists("Journal Entry", {"jf_expense_voucher": self.name}):
            frappe.throw("该费用登记已有总账凭证，请先核对")
        amount = validate_expense_voucher(self)
        offset = {"account": self.payment_account, "credit_in_account_currency": amount}
        if self.payment_method == CREDIT:
            offset.update(account=self.payable_account, party_type="Supplier", party=self.supplier)
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
                offset,
            ],
        })
        with expense_journal_action(journal, self.name):
            journal.insert()
            journal.submit()
        self.db_set("journal_entry", journal.name, update_modified=False)

    def before_cancel(self):
        journal, _ = expense_journal(self, lock=True)
        journal.check_permission("cancel")
        if self.payment_method == CREDIT:
            validate_credit_cancellation(self)

    def on_cancel(self):
        journal, _ = expense_journal(self, lock=True)
        journal.check_permission("cancel")
        with expense_journal_action(journal, self.name):
            journal.cancel()

    @frappe.whitelist()
    def credit_payment_context(self):
        return payment_context(self.name)
