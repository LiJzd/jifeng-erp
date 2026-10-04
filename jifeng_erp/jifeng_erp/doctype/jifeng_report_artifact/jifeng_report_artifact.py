from frappe.model.document import Document

from jifeng_erp import sample_source_report_artifacts as artifacts


class JifengReportArtifact(Document):
    def has_permission(self, permtype="read", *, debug=False, user=None):
        return artifacts.can_write_record(self, permtype, user)

    def before_insert(self):
        artifacts.validate_record_write(self)

    def before_validate(self):
        artifacts.validate_record_write(self)

    def validate(self):
        artifacts.validate_record_write(self)

    def before_rename(self, *args, **kwargs):
        artifacts.prevent_record_change()

    def on_trash(self):
        artifacts.prevent_record_change()

    def validate_share(self, share):
        artifacts.prevent_record_change()
