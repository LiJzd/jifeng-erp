"""Keep HRMS's automatic milestone claims idempotent without duplicate alerts."""

import frappe


class HRTelemetryMilestoneMixin:
    def db_insert(self, ignore_if_duplicate=False):
        # HRMS claims a milestone inside a savepoint and catches duplicate keys.
        # Native db_insert emits a dialog before that caught exception, although
        # payroll itself succeeds. Preserve the exception so the native claimant
        # still returns False and does not capture a second first-use event.
        automatic_claim = (
            self.doctype == "HR Telemetry Milestone"
            and getattr(self.flags, "ignore_permissions", False)
            and bool(self.get("event"))
            and self.name == self.get("event")
        )
        messages = frappe.local.message_log or []
        before = len(messages)
        try:
            return super().db_insert(ignore_if_duplicate=ignore_if_duplicate)
        except frappe.DuplicateEntryError:
            messages = frappe.local.message_log or []
            expected = frappe._("{0} {1} already exists").format(
                frappe._(self.doctype), frappe.bold(self.name)
            )
            if (automatic_claim and len(messages) == before + 1
                    and messages[-1].get("title") == frappe._("Duplicate Name")
                    and messages[-1].get("message") == expected):
                messages.pop()
            raise
