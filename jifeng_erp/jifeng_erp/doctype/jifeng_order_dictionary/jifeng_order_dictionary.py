from frappe.model.document import Document

from jifeng_erp.order_dictionary import (
    before_insert_dictionary,
    prevent_delete,
    validate_dictionary,
)


class JifengOrderDictionary(Document):
    def before_insert(self):
        before_insert_dictionary(self)

    def validate(self):
        validate_dictionary(self)

    def on_trash(self):
        prevent_delete(self)
