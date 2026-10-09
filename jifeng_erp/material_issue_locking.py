"""Acquire Flow locks before native Stock Entry parent and child locks.

Document.insert/save/submit/cancel call check_if_latest after their native
permission gate. Its superclass loads _doc_before_save FOR UPDATE. Keep that
native call intact, but first use the same Flow-first order as material detail
and draft creation. The preliminary persisted IDs may be from an older
REPEATABLE READ snapshot; the native current document must therefore be checked
afterwards, without acquiring any additional Flow lock at that point.

Callers that explicitly lock Stock Entry before entering this method must first
lock its Flow sources themselves in this order. This mixin cannot move locks
that the caller has already acquired.
"""

import frappe


_SOURCE_CONFLICT = "流程领料来源已发生变化，请刷新凭证后重新核对。"
_HEADER_CHANGED = "已有库存凭证的领料流程来源不可更改，请恢复原来源后重新核对。"
_RETURN_SOURCE = "领料退库来源已发生变化，请刷新凭证后重新核对。"
_RETURN_SOURCE_FIELD = "jf_material_return_against"


def _flow_id(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        frappe.throw(_SOURCE_CONFLICT, frappe.ValidationError)
    return value


def _flow_ids(doc):
    flows = {_flow_id(doc.get("jf_material_issue_flow"))}
    flows.update(_flow_id(row.get("jf_flow_order")) for row in doc.get("items") or [])
    flows.discard(None)
    return flows


def _persisted_flow_ids(name):
    # Internal existence read, deliberately without permission filtering or
    # row locks. Never return these IDs to the actor. The locked native document
    # below remains authoritative if this snapshot has missed a source change.
    rows = frappe.db.sql("""
        SELECT jf_material_issue_flow AS flow
        FROM `tabStock Entry` WHERE name = %s
        UNION ALL
        SELECT jf_flow_order AS flow
        FROM `tabStock Entry Detail`
        WHERE parent = %s AND parenttype = 'Stock Entry' AND parentfield = 'items'
    """, (name, name), as_dict=True)
    flows = {_flow_id(row.get("flow")) for row in rows}
    flows.discard(None)
    return flows


def _persisted_return_source(name):
    return frappe.db.get_value("Stock Entry", name, _RETURN_SOURCE_FIELD)


def _lock_issue_sources(source_names):
    """Lock source issues after all Flow rows and before the current entry."""
    try:
        for name in sorted({value for value in source_names if value}):
            parent = frappe.db.sql("""SELECT name FROM `tabStock Entry`
                WHERE name=%s FOR UPDATE NOWAIT""", (name,))
            if not parent:
                frappe.throw(_RETURN_SOURCE, frappe.ValidationError)
            frappe.db.sql("""SELECT name FROM `tabStock Entry Detail`
                WHERE parent=%s AND parenttype='Stock Entry' AND parentfield='items'
                ORDER BY name FOR UPDATE NOWAIT""", (name,))
    except (frappe.QueryTimeoutError, frappe.QueryDeadlockError):
        frappe.throw("原领料凭证正在被处理，请稍后刷新再试", frappe.ValidationError)


def _lock_flows(flow_ids):
    """Hold sorted scalar Flow locks until the caller's transaction ends."""
    for name in sorted(flow_ids):
        # This is an internal current read, not a grant to read Flow contents.
        # Existing native validation retains its own Flow permission checks.
        if not frappe.db.get_value("Jifeng Flow Order", name, "name", for_update=True):
            frappe.throw(_SOURCE_CONFLICT, frappe.ValidationError)


class MaterialIssueLockingMixin:
    def calculate_rate_and_amount(self, *args, **kwargs):
        if self.get(_RETURN_SOURCE_FIELD):
            # Native inward bundle calculation would otherwise copy the whole
            # row average into every selected batch/serial. Price this exact
            # source return before native basic amount and GL totals are built.
            from jifeng_erp.material_returns import validate_material_return
            validate_material_return(self)
        return super().calculate_rate_and_amount(*args, **kwargs)

    def check_if_latest(self):
        locked_flows = _flow_ids(self)
        current_return_source = self.get(_RETURN_SOURCE_FIELD)
        previous_return_source = None
        if not self.is_new():
            locked_flows.update(_persisted_flow_ids(self.name))
            previous_return_source = _persisted_return_source(self.name)
        return_sources = {current_return_source, previous_return_source}
        return_sources.discard(None)
        return_sources.discard("")
        for source_name in return_sources:
            locked_flows.update(_persisted_flow_ids(source_name))
        _lock_flows(locked_flows)

        # A return must serialize against the original issue, including the
        # submit/cancel path. The source lock is always taken before the native
        # lock on this return Stock Entry.
        _lock_issue_sources(return_sources)

        # Preserve native timestamp, state transition, and submit/cancel gates.
        result = super().check_if_latest()
        previous = self._doc_before_save
        if (not _flow_ids(self).issubset(locked_flows)
                or (previous and not _flow_ids(previous).issubset(locked_flows))):
            # Do not repair the order by taking a Flow lock while already
            # holding native Stock Entry locks. Abort with no source-ID leak.
            frappe.throw(_SOURCE_CONFLICT, frappe.TimestampMismatchError)
        if previous and _flow_id(previous.get("jf_material_issue_flow")) != _flow_id(
                self.get("jf_material_issue_flow")):
            # Read-only UI fields are still writable through native APIs.
            # Removing this tag would otherwise bypass the tagged issue cap.
            frappe.throw(_HEADER_CHANGED, frappe.ValidationError)
        if previous and previous.get(_RETURN_SOURCE_FIELD) != self.get(_RETURN_SOURCE_FIELD):
            frappe.throw(_RETURN_SOURCE, frappe.ValidationError)
        if previous and previous.get(_RETURN_SOURCE_FIELD) and previous.get(_RETURN_SOURCE_FIELD) not in return_sources:
            # A stale pre-lock snapshot did not include the current persisted
            # source. Abort instead of taking the source lock after the native
            # return document lock.
            frappe.throw(_RETURN_SOURCE, frappe.TimestampMismatchError)
        if (self.get("docstatus") == 2 and previous and previous.get("docstatus") == 1
                and self.get("stock_entry_type") == "Material Issue"):
            from jifeng_erp.material_returns import assert_no_material_returns
            assert_no_material_returns(self.name)
        return result
