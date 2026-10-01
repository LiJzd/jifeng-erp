from frappe.model.document import Document

from jifeng_erp.hr_dictionary import prevent_delete, validate_dictionary


class JifengHRDictionary(Document):
    def validate(self):
        validate_dictionary(self)

    def on_trash(self):
        prevent_delete(self)
