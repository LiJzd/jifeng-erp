from frappe.model.document import Document

from jifeng_erp.plating_color import (
    before_insert_dictionary,
    after_insert_dictionary,
    before_delete_dictionary,
    after_delete_dictionary,
    validate_dictionary,
)


class JifengPlatingColor(Document):
    def before_insert(self):
        before_insert_dictionary(self)

    def validate(self):
        validate_dictionary(self)

    def on_trash(self):
        before_delete_dictionary(self)

    def after_insert(self):
        after_insert_dictionary(self)

    def after_delete(self):
        after_delete_dictionary(self)
