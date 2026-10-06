"""Deny persisted output for the five fresh-only ledger reports.

Classification is independent of the consumer's permissions. It grants no read
and never treats a failed report lookup as an ordinary-report authorization.
"""

import copy

import frappe


DENIED = "此台账仅支持按当前权限重新查询，不支持读取或生成持久报表产物"
BINDING_DENIED = "此台账的报表及文件来源绑定不能解绑、改名或删除"
PUBLIC_DENIED = "存在此台账的公开文件或独立分享，需先核对并处理后再发布"
_CANONICAL_BINDING_FIELDS = (
    "name", "report_name", "report_type", "reference_report", "ref_doctype",
    "module", "is_standard", "report_script", "query", "snapshot_report",
)


def _fresh():
    from . import ledger_report_output
    return ledger_report_output


def _deny(message=DENIED):
    frappe.throw(message, frappe.PermissionError)


def is_ledger_report(name):
    return _fresh().resolve_target(name) is not None


def _stored(doctype, doc):
    name = doc.get("name")
    return frappe.get_doc(doctype, name) if name and frappe.db.exists(doctype, name) else None


def is_ledger_prepared(doc):
    # A caller may not turn a saved ledger PR into an ordinary PR by replacing
    # its in-memory report name before the native controller's permission gate.
    stored = _stored("Prepared Report", doc)
    return any(is_ledger_report(subject.get("report_name"))
               for subject in (doc, stored) if subject is not None)


def deny_prepared(doc):
    if is_ledger_prepared(doc):
        _deny()


def deny_prepared_name(name):
    deny_prepared(frappe.get_doc("Prepared Report", name))


def _file_subject_is_ledger(doc):
    parenttype, parent = doc.get("attached_to_doctype"), doc.get("attached_to_name")
    if parent and parenttype == "Prepared Report":
        if is_ledger_prepared(frappe.get_doc("Prepared Report", parent)):
            return True
    if parent and parenttype == "Report" and is_ledger_report(parent):
        return True
    # Retain the pre-existing immutable receipt binding as well as attachment
    # metadata. A signed legacy receipt must not bypass this fresh-only policy.
    from . import sample_source_report_artifacts as artifacts
    for name in artifacts.bound_roots(doc):
        artifact = frappe.get_doc("Jifeng Report Artifact", name)
        if is_ledger_report(artifact.get("report")):
            return True
    return False


def check_file_subjects(subjects):
    """Subjects are the existing File gate's current/persisted/path-alias union."""
    if any(_file_subject_is_ledger(doc) for doc in subjects if doc is not None):
        _deny()


def prevent_file_delete(doc, method=None):
    # Deletion needs five-ledger classification only. The native method unlinks
    # before doc events, so the ten-report byte/receipt read gate is unsuitable.
    from . import sample_source_report_files as files
    persisted = files._persisted(doc)
    subjects = [doc] + ([persisted] if persisted is not None else [])
    paths = {identity["path"] for subject in subjects
             if (identity := files._canonical(subject)) is not None}
    for path in sorted(paths):
        subjects.extend(files._alias_documents(path))
    check_file_subjects(subjects)


def prevent_prepared_delete(doc, *args, **kwargs):
    deny_prepared(doc)


def _cached_dependents(report_name):
    # Include outer aliases: repointing an inner link must not wash a File or PR
    # attached to its outer Custom Report. No role/read gate makes this scope.
    rows = frappe.get_all("Report", fields=["name", "report_type", "reference_report"],
                          limit_page_length=0)
    names = {report_name}
    while True:
        expanded = names | {row.name for row in rows if row.report_type == "Custom Report"
                            and row.reference_report in names}
        if expanded == names:
            break
        names = expanded
    if frappe.get_all("Prepared Report", filters={"report_name": ["in", sorted(names)]},
                      fields=["name"], limit_page_length=1):
        return True
    if frappe.get_all("File", filters={"attached_to_doctype": "Report",
                                        "attached_to_name": ["in", sorted(names)]},
                      fields=["name"], limit_page_length=1):
        return True
    # This is the immutable receipt's requested Report identity, not a guessed
    # display name or content hash. Missing physical bytes still remain bound.
    return bool(frappe.get_all("Jifeng Report Artifact", filters={"report": ["in", sorted(names)]},
                               fields=["name"], limit_page_length=1))


def before_validate_report(doc, method=None):
    stored = _stored("Report", doc)
    if stored is None:
        return
    if stored.name in _fresh().LEDGER_REPORTS:
        if any(stored.get(field) != doc.get(field) for field in _CANONICAL_BINDING_FIELDS):
            _deny(BINDING_DENIED)
        return
    before = _fresh().resolve_target(stored.name)
    if before is not None:
        after = _fresh().resolve_target(doc.name, root=doc)
        if (after is None or before.canonical_name != after.canonical_name) and _cached_dependents(stored.name):
            _deny(BINDING_DENIED)


def prevent_report_delete_or_rename(doc, *args, **kwargs):
    # Keeping the classification link is necessary for already stored PR/File
    # rows; Data attachment fields are not all rewritten by native rename_doc.
    stored = _stored("Report", doc)
    name = (stored or doc).get("name")
    if name in _fresh().LEDGER_REPORTS or (is_ledger_report(name) and _cached_dependents(name)):
        _deny(BINDING_DENIED)


def assert_ledger_output_publication_invariant():
    """Read-only release preflight. It never repairs, removes or republishes.

    Python File gates do not intercept already public static-file delivery.
    This preflight must pass before enabling the policy in a serving release.
    """
    from . import sample_source_report_files as files
    protected_files = set()
    for row in files._file_rows():
        doc = frappe.get_doc(doctype="File", **dict(row))
        persisted = files._persisted(doc)
        subjects = [doc] + ([persisted] if persisted is not None else [])
        identities = [files._canonical(subject) for subject in subjects]
        for identity in identities:
            if identity:
                subjects.extend(files._alias_documents(identity["path"]))
        if not any(_file_subject_is_ledger(subject) for subject in subjects):
            continue
        protected_files.update(subject.get("name") for subject in subjects if subject.get("name"))
        if any(not files._private(subject) or
               (files._canonical(subject) and not files._canonical(subject)["private"])
               for subject in subjects):
            _deny(PUBLIC_DENIED)
    for row in frappe.get_all("DocShare", fields=["share_doctype", "share_name"],
                              limit_page_length=0):
        if row.share_doctype == "File" and row.share_name in protected_files:
            _deny(PUBLIC_DENIED)
        if row.share_doctype == "Prepared Report":
            doc = frappe.get_doc("Prepared Report", row.share_name)
            if is_ledger_prepared(doc):
                _deny(PUBLIC_DENIED)


def before_job(method, kwargs):
    """Native execute_job supplies the exact normalized method name.

    Both old native jobs and the existing source-aware worker names are covered.
    This is not an arbitrary trusted-Python sandbox or a general queue policy.
    """
    prepared = {
        "frappe.core.doctype.prepared_report.prepared_report.generate_report": "prepared_report",
        "frappe.core.doctype.prepared_report.prepared_report.convert_json_to_csv": "prepared_report_name",
        "jifeng_erp.sample_source_prepared_reports.generate_report": "prepared_report",
        "jifeng_erp.sample_source_report_output.convert_json_to_csv": "prepared_report_name",
    }
    if method in prepared:
        deny_prepared_name(kwargs.get(prepared[method]))
    elif method in {"frappe.desk.query_report.run_export_query_job",
                    "jifeng_erp.sample_source_report_output.run_export_query_job"}:
        parameters = kwargs.get("form_params") or {}
        if not isinstance(parameters, dict):
            _deny()
        if is_ledger_report(parameters.get("report_name")):
            _deny()


def export_query():
    """Synchronous CSV/Excel only; the source frame includes final formatting."""
    from frappe.desk.utils import pop_csv_params, provide_binary_file
    from . import sample_source_report_output as output

    native = output._native()
    original = frappe._dict(frappe.local.form_dict)
    csv_params = pop_csv_params(original)
    native.clean_params(original)
    native.parse_json(original)
    parameters = output._export_parameters(original)
    context = _fresh().resolve_report(parameters.report_name)
    if context is None:
        _deny()
    frappe.permissions.can_export(context.report.ref_doctype, raise_exception=True)
    if int(parameters.export_in_background or 0):
        _deny()
    columns = frappe.parse_json(parameters.custom_columns or "[]")
    # Batch 1 owns this interface: transform must execute before its final
    # source/field/role/share/chain check, with finally resetting the frame.
    result = _fresh().generate_fresh(
        parameters.report_name, parameters.filters, custom_columns=columns,
        are_default_filters=False,
        transform=lambda payload: output._format_export(parameters, copy.deepcopy(csv_params), payload),
    )
    if result is None:
        frappe.respond_as_web_page(frappe._("No data to export"),
                                  frappe._("You can try changing the filters of your report."))
        return
    name, extension, content = result
    provide_binary_file(frappe._(name), extension, content)
