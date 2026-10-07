from frappe.model.document import Document

from jifeng_erp.customer_type import (
    after_delete_dictionary, after_insert_dictionary, before_delete_dictionary,
    before_insert_dictionary, validate_dictionary,
)


class JifengCustomerType(Document):
    def before_insert(self):
        before_insert_dictionary(self)

    def validate(self):
        validate_dictionary(self)

    def after_insert(self):
        after_insert_dictionary(self)

    def on_trash(self):
        before_delete_dictionary(self)

    def after_delete(self):
        after_delete_dictionary(self)
