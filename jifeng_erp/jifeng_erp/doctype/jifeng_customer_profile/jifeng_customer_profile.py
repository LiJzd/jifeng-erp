from frappe.model.document import Document


class JifengCustomerProfile(Document):
    def before_insert(self):
        from jifeng_erp.customer_archive import before_insert_profile
        before_insert_profile(self)

    def validate(self):
        from jifeng_erp.customer_archive import validate_profile
        validate_profile(self)

    def on_trash(self):
        from jifeng_erp.customer_archive import prevent_delete
        prevent_delete(self)
