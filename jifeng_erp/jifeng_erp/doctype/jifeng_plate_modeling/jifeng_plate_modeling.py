from frappe.model.document import Document

from jifeng_erp.plate_modeling import before_insert_record, prevent_rename, validate_record


class JifengPlateModeling(Document):
    def before_insert(self):
        before_insert_record(self)

    def validate(self):
        validate_record(self)

    def before_rename(self, old, new, merge=False):
        prevent_rename(self, old, new, merge)
