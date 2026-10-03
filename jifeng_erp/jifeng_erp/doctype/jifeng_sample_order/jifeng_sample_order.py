from frappe.model.document import Document


class JifengSampleOrder(Document):
    def validate(self):
        from jifeng_erp.sample_orders import validate_sample_order

        validate_sample_order(self)

    def before_submit(self):
        from jifeng_erp.sample_orders import before_submit_sample_order

        before_submit_sample_order(self)

    def before_cancel(self):
        from jifeng_erp.sample_orders import before_cancel_sample_order

        before_cancel_sample_order(self)

    def onload(self):
        from jifeng_erp.sample_orders import sample_order_onload

        sample_order_onload(self)

    def on_trash(self):
        from jifeng_erp.sample_orders import prevent_sample_order_delete

        prevent_sample_order_delete(self)

    def before_rename(self, *args, **kwargs):
        from jifeng_erp.sample_orders import prevent_sample_order_rename

        prevent_sample_order_rename(self, *args, **kwargs)
