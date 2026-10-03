import frappe
from frappe.model.document import Document

from jifeng_erp.salary_history_classification import (
    before_cancel_rule,
    before_submit_rule,
    prevent_rule_delete,
    prevent_rule_rename,
    prevent_rule_update,
    publish_historical,
    validate_rule,
)


class JifengSalaryHistoryRule(Document):
    def validate(self):
        validate_rule(self)

    def before_submit(self):
        before_submit_rule(self)

    def before_update_after_submit(self):
        prevent_rule_update(self)

    def before_cancel(self):
        before_cancel_rule(self)

    def on_trash(self):
        prevent_rule_delete(self)

    def before_rename(self, *args, **kwargs):
        prevent_rule_rename(self, *args, **kwargs)

    @frappe.whitelist()
    def publish_historical(self, reason):
        return publish_historical(self, reason)
