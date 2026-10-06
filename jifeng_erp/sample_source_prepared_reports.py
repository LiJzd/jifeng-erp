"""Exact report snapshots: native ordinary behavior, deny-only sealed reads.

This module does not repair legacy snapshots, confer Report permissions, or
commit a successful generation itself. The fixed RQ worker starts its generation
frame only after native update_job_id's commit. Full rollback, rather than a
savepoint, is required for native File rollback callbacks.
"""

from contextvars import ContextVar
import gzip
import hashlib
import json
import resource
import uuid

import frappe

from jifeng_erp.sample_orders import _freeze


PREPARED = "Prepared Report"
LEGACY_MESSAGE = "旧快照缺少来源校验，请按原筛选重新生成"
DENIED = "无法完整读取报表及其来源，请沿用来源单据的角色、公司和字段权限"
SHARE_DENIED = "来源报表及其产物不能独立分享，请沿用来源单据的角色、公司和字段权限"
GENERATION_ERROR = "报表生成失败，请核对当前报表和来源权限后重新生成"
_NATIVE_GUARDS = ContextVar("jifeng_prepared_native_permissions", default=frozenset())
_STATUS_SAVE = ContextVar("jifeng_prepared_fixed_status_save", default=None)
_STATUS_SAVE_MARKER = object()
_ROLLBACK_UNKNOWN = object()


def _artifacts():
    from jifeng_erp import sample_source_report_artifacts
    return sample_source_report_artifacts


def _native():
    from frappe.core.doctype.prepared_report import prepared_report
    return prepared_report


def _deny(message=DENIED):
    frappe.throw(message, frappe.PermissionError)


def _current_user(user=None):
    user = user or frappe.session.user
    if user != frappe.session.user or user == "Guest":
        _deny()
    return user


def _persisted(doc, *, lock=False):
    if doc.doctype != PREPARED or not doc.get("name"):
        _deny()
    return frappe.get_doc(PREPARED, doc.name, for_update=lock)


def _roots(doc):
    return _artifacts().artifacts_for_prepared(doc.name) if doc.get("name") else []


def _scoped(doc):
    from .ledger_report_cache import deny_prepared
    deny_prepared(doc)
    # An edited report name cannot make an already bound artifact ordinary.
    return bool(_roots(doc) or _artifacts().resolve_report(doc.get("report_name")))


def _unshared(doc):
    # Named new documents also need this check: a ghost share must not become a
    # grant when insert later assigns the same identity.
    if doc.get("name") and frappe.db.exists("DocShare", {
            "share_doctype": PREPARED, "share_name": doc.name}):
        _deny(SHARE_DENIED)


def _native_permission(doc, ptype, user):
    key = (doc.get("name"), ptype, user)
    token = _NATIVE_GUARDS.set(_NATIVE_GUARDS.get() | {key})
    try:
        return frappe.has_permission(PREPARED, ptype, doc=doc, user=user, throw=False)
    finally:
        _NATIVE_GUARDS.reset(token)


def _same(before, after):
    if _freeze(before.as_dict()) != _freeze(after.as_dict()):
        _deny()


def _binding(doc):
    try:
        filters = json.loads(doc.filters) if isinstance(doc.filters, str) else doc.filters
        if filters is None or filters == []:
            filters = {}
        if not isinstance(filters, dict):
            _deny()
        return doc.report_name, doc.owner, _freeze(filters)
    except (ValueError, TypeError):
        _deny()


def _worker_record(name):
    """Read the saved job target without resolving its now-revoked Report."""
    if not isinstance(name, str) or not name:
        _deny()
    doc = frappe.get_doc(PREPARED, name, for_update=True)
    if doc.doctype != PREPARED or doc.name != name or doc.is_new():
        _deny()
    return doc


def _worker_binding(doc):
    # Preserve even invalid filter text: a failed classifier may record only a
    # generic status, and may not normalize or replace that persisted binding.
    return _freeze((doc.doctype, doc.name, doc.get("report_name"),
                    doc.get("filters"), doc.get("owner")))


def _status_write(doc, ptype, user):
    """Only the exact fixed worker object can perform these status writes.

    Completed still checks the sealed artifact's full source gate. Error is a
    generic status after full rollback, with no output and no altered binding;
    requiring a revoked source read here would prevent recording the denial.
    Neither case grants a read, create, share, or arbitrary ignore-permission save.
    """
    context = _STATUS_SAVE.get()
    if not (isinstance(context, dict) and context.get("marker") is _STATUS_SAVE_MARKER
            and context["doc"] is doc and ptype == "write"
            and context["user"] == user == frappe.session.user):
        return False
    if doc.doctype != PREPARED or doc.is_new() or user == "Guest":
        _deny()
    stored = _persisted(doc)
    _unshared(stored)
    _unshared(doc)
    if (stored.doctype != PREPARED or stored.name != doc.name or stored.is_new()
            or _worker_binding(stored) != _worker_binding(doc) or doc.owner != user):
        _deny()
    ignored = {"status", "error_message", "modified", "modified_by"}
    if context["kind"] == "Completed":
        frame = context["frame"]
        if not (frame is _artifacts().current_frame() and frame.artifact_kind == "JSON"
                and frame.prepared_report == doc.name and frame.user == user
                and frame.sealed_artifact is not None
                and doc.status == "Completed" and not doc.get("error_message")
                and stored.status == "Started"):
            _deny()
        proof = _checked_json(stored)
        if proof.artifact.name != frame.sealed_artifact.name:
            _deny()
        ignored.update({"report_end_time", "peak_memory_usage"})
    elif context["kind"] == "Error":
        if not (stored.status in ("Queued", "Started") and doc.status == "Error"
                and doc.error_message == GENERATION_ERROR and not _roots(stored)
                and context.get("failed_binding") == _worker_binding(stored)):
            _deny()
    else:
        _deny()
    if _freeze({key: value for key, value in stored.as_dict().items() if key not in ignored}) != (
            _freeze({key: value for key, value in doc.as_dict().items() if key not in ignored})):
        _deny()
    _same(stored, _persisted(doc))
    _unshared(doc)
    return True


def _save_status(doc, kind, *, frame=None, failed_binding=None):
    token = _STATUS_SAVE.set({"marker": _STATUS_SAVE_MARKER, "doc": doc, "kind": kind,
                              "frame": frame, "user": frappe.session.user,
                              "failed_binding": failed_binding})
    try:
        doc.save(ignore_permissions=True)
    finally:
        _STATUS_SAVE.reset(token)


def _checked_json(doc):
    """Return the sole bound JSON proof; do not select by attachment order."""
    names = _roots(doc)
    if not names:
        _deny(LEGACY_MESSAGE)
    proofs = [_artifacts().check_artifact(name) for name in names]
    matches = [proof for proof in proofs if proof.artifact.get("artifact_kind") == "JSON"]
    if len(matches) != 1:
        _deny()
    proof = matches[0]
    if proof.prepared_report != doc.name or proof.report_name != doc.report_name:
        _deny()
    return proof


def _check_metadata(doc, user=None):
    _current_user(user)
    _unshared(doc)
    if doc.get("status") == "Completed" or _roots(doc):
        if doc.get("status") != "Completed":
            _deny()
        _checked_json(doc)
    else:
        # Old error_message may contain native with_context locals. Never expose
        # it merely because the report's current filters are readable.
        if doc.get("error_message") not in (None, "", GENERATION_ERROR):
            _deny(LEGACY_MESSAGE)
        if doc.get("status") not in ("Queued", "Started", "Error", "Failed", "Cancelled"):
            _deny()
        if _artifacts().capture(doc.report_name, doc.filters) is None:
            _deny()
    _unshared(doc)


def _read(doc, *, with_file_name=False, allow_owner=False):
    user = _current_user()
    stored = _persisted(doc)
    if not _scoped(stored):
        _deny()
    if not (allow_owner and stored.owner == user or _native_permission(stored, "read", user)):
        _deny()
    _unshared(stored)
    if stored.get("status") != "Completed":
        _deny(LEGACY_MESSAGE)
    proof = _checked_json(stored)
    # C's content boundary also checks all aliases/roots. A must additionally
    # verify the exact returned bytes and decompressed full payload.
    content = proof.file_doc.get_content(encodings=())
    verified = _artifacts().check_artifact(proof.artifact.name,
                                         file_doc=proof.file_doc, content=content)
    if not verified.content_verified or verified.payload is None:
        _deny()
    final = _persisted(doc)
    _same(stored, final)
    if not (allow_owner and final.owner == user or _native_permission(final, "read", user)):
        _deny()
    _unshared(final)
    # This final proof follows native permission and PR freeze, rather than
    # returning an earlier pre-source check as a completed byte authorization.
    verified = _artifacts().check_artifact(verified.artifact.name,
                                         file_doc=verified.file_doc, content=content)
    if not verified.content_verified or verified.payload is None:
        _deny()
    final = _persisted(doc)
    _same(stored, final)
    if not (allow_owner and final.owner == user or _native_permission(final, "read", user)):
        _deny()
    _unshared(final)
    return ((verified.payload, verified.file_name) if with_file_name else verified.payload)


def prepared_report_has_permission(doc, ptype="read", user=None, debug=False):
    """Controller deny-only hook; native hooks/roles still make the grant."""
    try:
        from .ledger_report_cache import deny_prepared
        deny_prepared(doc)
    except Exception:
        return False
    user = user or frappe.session.user
    if (doc.get("name"), ptype, user) in _NATIVE_GUARDS.get():
        return True
    try:
        if _status_write(doc, ptype, user):
            return True
        stored = doc if doc.is_new() else _persisted(doc)
        if not _scoped(stored):
            return True
        _current_user(user)
        if doc.is_new():
            if doc.get("owner") not in (None, "", user):
                _deny()
            _unshared(doc)
            if _artifacts().capture(doc.report_name, doc.filters) is None:
                _deny()
        else:
            _check_metadata(stored, user)
            _same(stored, _persisted(doc))
        return True
    except Exception:
        # No private report/source identifiers in permission diagnostics.
        return False


def prepared_report_query_conditions(user=None):
    user = user or frappe.session.user
    names = []
    for row in frappe.get_all(PREPARED, fields=["name"], limit_page_length=0):
        doc = frappe.get_doc(PREPARED, row.name)
        try:
            scoped = _scoped(doc)
        except Exception:
            # resolve_report also enforces Report authority. A hidden report
            # cannot put its name in debug SQL, nor stop readable ordinary rows.
            # A share must not revive a row whose scope cannot be established.
            _unshared(doc)
            continue
        if scoped:
            # Share-only native queries can skip PQC. Deployment must prove
            # assert_prepared_share_invariant before these hooks are sufficient.
            _unshared(doc)
        if not (_native_permission(doc, "read", user)
                or not scoped and _native_permission(doc, "select", user)):
            continue
        if not scoped or prepared_report_has_permission(doc, "read", user):
            names.append(row.name)
    if not names:
        return "1=0"
    return "`tabPrepared Report`.`name` in (" + ",".join(
        frappe.db.escape(name) for name in names) + ")"


def before_validate_prepared_report(doc, method=None):
    """Serialize changing a binding with native independent sharing."""
    if doc.doctype != PREPARED:
        return
    from .ledger_report_cache import deny_prepared
    deny_prepared(doc)
    if _status_write(doc, "write", frappe.session.user):
        # Reacquire the same serialization lock used by DocShare validation.
        _unshared(_persisted(doc, lock=True))
        return
    stored = None if doc.is_new() else _persisted(doc, lock=True)
    old_scope = stored is not None and _scoped(stored)
    if old_scope and _binding(stored) != _binding(doc):
        # Legacy unindexed attachments must not become ordinary by editing the
        # native PR's readonly (but not server-immutable) report/filter fields.
        _deny()
    if old_scope or _scoped(doc):
        _unshared(doc)
        if stored is not None:
            _unshared(stored)


def validate_prepared_report_share(doc, method=None, share=None):
    from .ledger_report_cache import deny_prepared
    deny_prepared(doc)
    current = doc if doc.is_new() else _persisted(doc, lock=True)
    if _scoped(current):
        _deny(SHARE_DENIED)


def before_validate_docshare(doc, method=None):
    if doc.get("share_doctype") != PREPARED:
        return
    name = doc.get("share_name")
    if not name or not frappe.db.exists(PREPARED, name):
        _deny(SHARE_DENIED)
    validate_prepared_report_share(frappe.get_doc(PREPARED, name, for_update=True))


def assert_prepared_share_invariant():
    """Read-only release preflight, never delete/revoke historical shares."""
    for row in frappe.get_all("DocShare", filters={"share_doctype": PREPARED},
                              fields=["share_name"], limit_page_length=0):
        if not row.share_name or not frappe.db.exists(PREPARED, row.share_name):
            _deny(SHARE_DENIED)
        if _scoped(frappe.get_doc(PREPARED, row.share_name)):
            _deny(SHARE_DENIED)


class SampleSourcePreparedReportMixin:
    def on_trash(self):
        from .ledger_report_cache import deny_prepared
        deny_prepared(self)
        return super().on_trash()

    def has_permission(self, permtype="read", *, debug=False, user=None):
        user = user or frappe.session.user
        try:
            from .ledger_report_cache import deny_prepared
            deny_prepared(self)
            if _status_write(self, permtype, user):
                return bool(super().has_permission(permtype, debug=debug, user=user))
            stored = self if self.is_new() else _persisted(self)
            scoped = _scoped(stored)
        except Exception:
            return False
        if not scoped:
            return super().has_permission(permtype, debug=debug, user=user)
        key = (self.get("name"), permtype, user)
        token = _NATIVE_GUARDS.set(_NATIVE_GUARDS.get() | {key})
        try:
            allowed = super().has_permission(permtype, debug=debug, user=user)
        finally:
            _NATIVE_GUARDS.reset(token)
        return bool(allowed and prepared_report_has_permission(self, permtype, user, debug))

    def get_prepared_data(self, with_file_name=False):
        from .ledger_report_cache import deny_prepared
        deny_prepared(self)
        if not _scoped(_persisted(self)):
            return super().get_prepared_data(with_file_name=with_file_name)
        return _read(self, with_file_name=with_file_name)

    def after_insert(self):
        if not _scoped(self):
            return super().after_insert()
        user = _current_user()
        stored = _persisted(self, lock=True)
        if stored.owner != user or stored.get("status") != "Queued":
            _deny()
        _check_metadata(stored, user)
        _same(stored, _persisted(self))
        from frappe.utils.background_jobs import enqueue
        timeout = frappe.get_value("Report", stored.report_name, "timeout")
        enqueue(generate_report, queue="long", prepared_report=stored.name,
                timeout=timeout or _native().REPORT_TIMEOUT,
                enqueue_after_commit=True, at_front_when_starved=True)


def _generation_options(resolved):
    return {"generation": {
        "saved_custom_columns": resolved.report.get("custom_columns") or [],
        "custom_columns": [], "is_tree": False, "parent_field": None,
    }}


def _write_json(frame, result, report_name):
    # These are the native create_json_gz_file encoding/compression semantics.
    payload = frappe.safe_encode(frappe.as_json(result, indent=None, separators=(",", ":")))
    content = gzip.compress(payload, compresslevel=5)
    filename = "{}_{}.json.gz".format(
        frappe.scrub(report_name), frappe.utils.data.format_datetime(frappe.utils.now(), "Y-m-d-H-M"))
    file_doc = _artifacts().new_pending_file(frame, payload=payload,
                                            content=content, file_name=filename)
    file_doc.save(ignore_permissions=True)
    _artifacts().seal(frame, file_doc)
    return file_doc


def _notification(instance, report):
    frappe.get_doc({"doctype": "Notification Log",
                    "subject": f"{instance.report_name} report is ready.",
                    "for_user": frappe.session.user, "type": "Alert",
                    "document_type": "Report", "document_name": report.name,
                    "link": f"/desk/query-report/{report.name}?prepared_report_name={instance.name}",
                    }).insert(ignore_permissions=True)


def _failed(prepared_report, binding):
    # Native File.on_rollback is registered on the full transaction callback;
    # rolling back only a savepoint would leave private blobs behind.
    frappe.db.rollback()
    instance = _worker_record(prepared_report)
    if (instance.owner != frappe.session.user or instance.get("status") not in ("Queued", "Started")
            or _worker_binding(instance) != binding):
        _deny()
    instance.status = "Error"
    instance.error_message = GENERATION_ERROR
    _save_status(instance, "Error", failed_binding=binding)


def _failed_or_rollback(prepared_report, binding):
    """Finish sensitive failures here; only an unknown sentinel leaves this body."""
    try:
        if binding is not None:
            _failed(prepared_report, binding)
        else:
            frappe.db.rollback()
    except Exception:
        return _ROLLBACK_UNKNOWN
    return None


def _generate_report_body(prepared_report):
    """Sensitive result/frame locals stay in this private, finished body."""
    binding = None
    try:
        instance = _worker_record(prepared_report)
        from .ledger_report_cache import deny_prepared
        deny_prepared(instance)
        actor = frappe.session.user
        if (isinstance(actor, str) and actor and actor != "Guest" and instance.owner == actor
                and instance.get("status") in ("Queued", "Started")):
            binding = _worker_binding(instance)
        scoped = _scoped(instance)
    except Exception:
        return _failed_or_rollback(prepared_report, binding)
    if not scoped:
        return _native().generate_report(prepared_report)
    try:
        user = _current_user()
        if instance.owner != user or instance.get("status") != "Queued":
            return None
        binding = _worker_binding(instance)
        _check_metadata(instance, user)
        _same(instance, _persisted(instance))
        # The native early commit is deliberately outside the generation frame.
        _native().update_job_id(prepared_report)
        instance = _worker_record(prepared_report)
        if (instance.owner != user or instance.get("status") != "Started"
                or _worker_binding(instance) != binding):
            _deny()
        resolved = _artifacts().resolve_report(instance.report_name)
        if resolved is None or _roots(instance):
            _deny()
        _unshared(instance)
        with _artifacts().generation(instance.report_name, instance.filters,
                                    prepared_report=instance.name,
                                    output_options=_generation_options(resolved)) as frame:
            from jifeng_erp.sample_source_report_output import generate_report_result
            from frappe.monitor import add_data_to_monitor
            add_data_to_monitor(report=instance.report_name)
            result = generate_report_result(frame.resolved.report, filters=instance.filters,
                                            user=user, context=frame.resolved)
            _same(instance, _persisted(instance))
            _unshared(instance)
            _write_json(frame, result, instance.report_name)
            _same(instance, _persisted(instance))
            _unshared(instance)
            instance.status = "Completed"
            instance.error_message = None
            instance.report_end_time = frappe.utils.now()
            instance.peak_memory_usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            add_data_to_monitor(peak_memory_usage=instance.peak_memory_usage)
            _save_status(instance, "Completed", frame=frame)
            _notification(instance, frame.resolved.report)
        frappe.publish_realtime("report_generated", {"report_name": instance.report_name,
                                                     "name": instance.name}, user=user)
    except Exception:
        # Neither native _save_error(with_context=True) nor RQ's generic exception
        # logger should receive source rows/locals from this fixed worker.
        return _failed_or_rollback(prepared_report, binding)


def generate_report(prepared_report):
    """Fixed RQ wrapper: unknown rollback must never enter native success commit.

    An unknown cleanup leaves only this minimal wrapper in the generic error
    traceback. The completed body's result/frame/filter locals cannot be logged
    by native execute_job. No proof or bypass parameter is accepted.
    """
    result = _generate_report_body(prepared_report)
    if result is _ROLLBACK_UNKNOWN:
        raise frappe.PermissionError(GENERATION_ERROR) from None
    return result


def get_prepared_report_result(report, filters, dn="", user=None):
    """Native DTO shape, scoped read errors escape without doc.log_error."""
    name = report.get("custom_report") or report.get("report_name") or report.name
    from .ledger_report_cache import is_ledger_report, deny_prepared_name, _deny
    if is_ledger_report(name):
        _deny()
    if dn:
        deny_prepared_name(dn)
    requested = _artifacts().resolve_report(name)
    if requested is None:
        from frappe.desk.query_report import get_prepared_report_result as native_result
        return native_result(report, filters, dn, user)
    user = _current_user(user)
    if not dn:
        dn = _native().get_completed_prepared_report(filters, user, name)
    if not dn:
        return {"prepared_report": True, "doc": None}
    doc = frappe.get_doc(PREPARED, dn)
    actual = _artifacts().resolve_report(doc.report_name)
    if actual is None or actual.canonical_name != requested.canonical_name:
        _deny()
    payload = _read(doc, allow_owner=True)
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict):
        # New artifacts contain the full native result object. Legacy list files
        # cannot acquire provenance through compatibility conversion.
        _deny(LEGACY_MESSAGE)
    columns = data.get("columns")
    if not isinstance(columns, list):
        _deny()
    for column in columns:
        if isinstance(column, dict) and column.get("label"):
            column["label"] = frappe._(column["label"])
    return data | {"columns": columns, "prepared_report": True, "doc": doc}


def _regeneration_name(old, user, request_id):
    try:
        parsed = uuid.UUID(request_id)
        if str(parsed) != request_id.lower():
            _deny()
    except (ValueError, TypeError, AttributeError):
        _deny()
    # The nonce only names a row; every access still performs current source and
    # native authority checks. Different users/old identities cannot collide.
    encoded = json.dumps([user, old.name, str(parsed)], ensure_ascii=False).encode()
    return "jf-regenerated-" + hashlib.sha256(encoded).hexdigest()[:48]


def _existing_regeneration(name, old, user):
    active = frappe.get_doc(PREPARED, name)
    if (active.name == old.name or active.report_name != old.report_name
            or active.owner != user or _binding(active)[2] != _binding(old)[2]
            or not (active.owner == user or _native_permission(active, "read", user))):
        _deny()
    _check_metadata(active, user)
    if active.status == "Completed":
        _read(active, allow_owner=True)
    elif active.status not in ("Queued", "Started"):
        _deny(GENERATION_ERROR)
    _same(active, _persisted(active))
    _same(old, _persisted(old))
    _unshared(old)
    return {"name": active.name}


@frappe.whitelist()
def regenerate_prepared_report(prepared_report_name: str, regeneration_request_id: str | None = None):
    """Create a new identity/time from an old persisted filter set, not its bytes."""
    user = _current_user()
    old = frappe.get_doc(PREPARED, prepared_report_name)
    if not _scoped(old) or not (old.owner == user or _native_permission(old, "read", user)):
        _deny()
    _unshared(old)
    if _artifacts().capture(old.report_name, old.filters) is None:
        _deny()
    _same(old, _persisted(old))
    name = (_regeneration_name(old, user, regeneration_request_id)
            if regeneration_request_id is not None else None)
    if name and frappe.db.exists(PREPARED, name):
        return _existing_regeneration(name, old, user)
    new = frappe.get_doc({"doctype": PREPARED, "report_name": old.report_name,
                          "filters": _native().process_filters_for_prepared_report(old.filters)})
    try:
        new.insert(ignore_permissions=True, **({"set_name": name} if name else {}))
    except frappe.DuplicateEntryError:
        if not name or not frappe.db.exists(PREPARED, name):
            raise
        return _existing_regeneration(name, old, user)
    return {"name": new.name}


@frappe.whitelist()
def get_regeneration_target(report_name: str, prepared_report_name: str | None = None,
                            filters: str | dict | None = None, are_default_filters: bool = False):
    """Authorize a regeneration button without returning old bytes or metadata.

    The request filters select an implicit native cache entry only. The actual
    target's persisted filters and report chain govern authorization and the
    later regeneration. Failed eligibility is intentionally just false.
    """
    try:
        user = _current_user()
        requested = _artifacts().resolve_report(report_name)
        if requested is None:
            return {"eligible": False}
        if are_default_filters and requested.report.get("custom_filters"):
            filters = requested.report.get("custom_filters")
        name = prepared_report_name or _native().get_completed_prepared_report(filters, user, report_name)
        if not name:
            return {"eligible": False}
        old = frappe.get_doc(PREPARED, name)
        actual = _artifacts().resolve_report(old.report_name)
        if (actual is None or actual.canonical_name != requested.canonical_name
                or old.status != "Completed"
                or not (old.owner == user or _native_permission(old, "read", user))):
            return {"eligible": False}
        _unshared(old)
        if _artifacts().capture(old.report_name, old.filters) is None:
            return {"eligible": False}
        _same(old, _persisted(old))
        _unshared(old)
        if old.owner != user and not _native_permission(old, "read", user):
            return {"eligible": False}
        return {"eligible": True, "name": old.name}
    except Exception:
        return {"eligible": False}


@frappe.whitelist()
def get_reports_in_queued_state(report_name: str, filters):
    from .ledger_report_cache import is_ledger_report, _deny
    if is_ledger_report(report_name):
        _deny()
    if _artifacts().resolve_report(report_name) is None:
        return _native().get_reports_in_queued_state(report_name, filters)
    _current_user()
    rows = _native().get_reports_in_queued_state(report_name, filters)
    result = []
    for row in rows:
        doc = frappe.get_doc(PREPARED, row.name)
        if doc.report_name != report_name or doc.owner != frappe.session.user:
            _deny()
        _check_metadata(doc)
        _same(doc, _persisted(doc))
        result.append(row)
    return result
