from frappe.model.document import Document

from jifeng_erp.customer_type import prevent_receipt_change, validate_receipt


class JifengCustomerTypeReceipt(Document):
    def validate(self):
        validate_receipt(self)

    def on_trash(self):
        prevent_receipt_change(self)

    def before_rename(self, *args, **kwargs):
        prevent_receipt_change(self)
