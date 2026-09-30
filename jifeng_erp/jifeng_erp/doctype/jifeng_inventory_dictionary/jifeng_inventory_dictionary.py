from frappe.model.document import Document

from jifeng_erp.inventory_archive import validate_dictionary


class JifengInventoryDictionary(Document):
    def validate(self):
        validate_dictionary(self)
