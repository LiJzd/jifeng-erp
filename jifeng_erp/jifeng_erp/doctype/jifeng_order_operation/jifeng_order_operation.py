from frappe.model.document import Document

from jifeng_erp.order_operations import (
    before_insert_ordering,
    prevent_delete,
    validate_ordering,
)


class JifengOrderOperation(Document):
    def before_insert(self):
        before_insert_ordering(self)

    def validate(self):
        validate_ordering(self)

    def on_trash(self):
        prevent_delete(self)
