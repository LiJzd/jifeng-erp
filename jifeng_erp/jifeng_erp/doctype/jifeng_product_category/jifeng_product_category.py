from frappe.model.document import Document

from jifeng_erp.product_category import before_insert_record, prevent_delete, prevent_rename, validate_record


class JifengProductCategory(Document):
    def before_insert(self):
        before_insert_record(self)

    def validate(self):
        validate_record(self)

    def on_trash(self):
        prevent_delete(self)

    def before_rename(self, old, new, merge=False):
        prevent_rename(self, old, new, merge)
