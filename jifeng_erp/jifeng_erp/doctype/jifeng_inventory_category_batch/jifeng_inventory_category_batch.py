from frappe.model.document import Document

from jifeng_erp.inventory_category import prevent_receipt_change, validate_batch_receipt


class JifengInventoryCategoryBatch(Document):
    def before_validate(self):
        validate_batch_receipt(self)

    def validate(self):
        validate_batch_receipt(self)

    def on_trash(self):
        prevent_receipt_change()

    def before_rename(self, *args, **kwargs):
        prevent_receipt_change()
