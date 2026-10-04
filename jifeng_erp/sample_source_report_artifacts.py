"""Server-owned receipts for the ten exact source-aware Report definitions.

There are no public methods, caller-supplied authorization tokens, commits, or
cache writes here. Fixed generators own the short-lived frame. Native ordinary
reports remain outside this boundary; ordinary contributors to a known report
are nevertheless inventoried and reclassified on every later consumption.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import copy
import gzip
import hashlib
import json
from pathlib import Path
import re
import uuid

import frappe

from jifeng_erp import sample_source_reports as reports
from jifeng_erp.sample_orders import _freeze


DOCTYPE = "Jifeng Report Artifact"
VERSION = "jifeng-report-artifact-v1"
LEGACY_MESSAGE = "旧快照缺少来源校验，请按原筛选重新生成"
_DENIED = "无法完整核对报表产物及其来源，请重新生成"
_FRAME = ContextVar("jifeng_report_artifact_generation", default=None)
_CONSUMING = ContextVar("jifeng_report_artifact_consuming", default=frozenset())
_KINDS = frozenset({"Execution", "JSON", "CSV", "Export"})
_DEFINITION_FIELDS = (
    "name", "report_name", "report_type", "reference_report", "ref_doctype",
    "module", "is_standard", "disabled", "roles", "columns", "filters",
    "query", "report_script", "json", "add_total_row", "add_translate_data",
    "snapshot_report", "doctype_to_sync",
)
_FILE_FIELDS = ("name", "file_name", "file_url", "is_private", "is_folder",
                "attached_to_doctype", "attached_to_name", "attached_to_field", "folder", "owner")
_RECORD_FIELDS = ("name", "state", "artifact_kind", "report", "prepared_report",
                  "file", "source_artifact", "canonical_path_sha256", "receipt")


def _deny(message=_DENIED):
    frappe.throw(message, frappe.PermissionError)


def _require(condition, message=_DENIED):
    if not condition:
        _deny(message)


def _actor():
    user = frappe.session.user
    _require(isinstance(user, str) and user and user != "Guest")
    return user


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(_freeze(value)).encode("utf-8")).hexdigest()


def _bytes_digest(content):
    _require(isinstance(content, bytes))
    return hashlib.sha256(content).hexdigest()


def _mapping(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            _deny()
    if value is None or value == []:
        value = {}
    _require(isinstance(value, dict))
    try:
        return json.loads(_json(value))
    except (ValueError, TypeError):
        _deny()


def _report(name):
    _require(isinstance(name, str) and name)
    try:
        doc = frappe.get_doc("Report", name)
    except frappe.DoesNotExistError:
        _deny()
    _require(doc.doctype == "Report" and doc.name == name)
    return doc


@dataclass(frozen=True)
class ResolvedReport:
    requested_name: str
    canonical_name: str
    report: object
    spec: dict
    chain: tuple


def resolve_report(name):
    """Match native recursive reference resolution, then authorize exact scope.

    Runtime timing/prepared/modified fields are not output definitions. The
    outer Custom JSON alone supplies saved columns/filters, as get_report_doc.
    """
    documents, seen = [], set()
    while True:
        _require(name not in seen)
        seen.add(name)
        doc = _report(name)
        documents.append(doc)
        if doc.get("report_type") != "Custom Report":
            break
        name = doc.get("reference_report")
    if doc.name not in reports.KNOWN_REPORTS:
        return None
    user = _actor()
    spec = reports._spec(doc.name)
    _require(spec is not None)
    # Native get_report_doc applies Report.is_permitted and refdoctype.report,
    # rather than requiring System Manager's document-level Report.read.
    _require(doc.is_permitted() and not doc.get("disabled")
             and frappe.has_permission(doc.ref_doctype, "report", user=user))
    definition = reports._definition_pair(doc, spec["kind"], spec)
    chain = tuple((entry.name, _digest({
        **{key: entry.get(key) for key in _DEFINITION_FIELDS},
        **({"prepared_report": entry.get("prepared_report")}
           if entry.get("report_type") == "Custom Report" else {}),
    }))
                  for entry in documents)
    doc.custom_columns = []
    doc.custom_filters = []
    if len(documents) > 1:
        outer = documents[0]
        data = _mapping(outer.get("json"))
        doc.custom_columns = copy.deepcopy(data.get("columns") or [])
        doc.custom_filters = copy.deepcopy(data.get("filters") or [])
        doc.custom_report = outer.name
        doc.is_custom_report = True
        # Native get_report_doc follows the outer Custom's explicit switch;
        # the canonical Report's own automatic prepared flag is not inherited.
        doc.prepared_report = outer.get("prepared_report")
    return ResolvedReport(documents[0].name, doc.name, doc, spec,
                          (chain, _digest(definition)))


def _state(doctype, name, *, required=False):
    doc = reports.sources._reference(doctype, name)
    if doc is None:
        _require(not required)
        return {"doctype": doctype, "name": name, "missing": True}
    return {"doctype": doctype, "name": name, "state_sha256": _digest(doc)}


def _inventory(scope):
    """Freeze all raw contributors/dependencies without ordinary read grants."""
    parents = []
    for doctype, name in sorted(set(scope.parents)):
        state = _state(doctype, name, required=True)
        doc = reports.sources._reference(doctype, name)
        state["protected"] = bool(reports.sources._scope(doc))
        parents.append(state)
    dependencies = []
    for dep in scope.dependencies:
        _require(isinstance(dep, reports.ReportDependency) and dep.owner in scope.parents)
        if dep.kind == "rows":
            raw = frappe.get_all(dep.doctype, filters=dep.filters,
                                 fields=sorted({"name", "modified", *dep.fields}), order_by="name asc")
            states = [_state(dep.doctype, row["name"]) for row in raw]
        else:
            _require(dep.kind == "document")
            raw = []
            states = [_state(dep.doctype, dep.name)]
        dependencies.append({"owner": list(dep.owner), "doctype": dep.doctype,
                             "kind": dep.kind, "filters_sha256": _digest(dep.filters),
                             "fields": list(dep.fields), "rows_sha256": _digest(raw),
                             "states": states, "name": dep.name,
                             "parenttype": dep.parenttype, "bound_child": dep.bound_child,
                             "bindings_sha256": _digest(dep.bindings), "same_company": dep.same_company,
                             "native_read": dep.native_read, "observe_only": dep.observe_only})
    return {"parents": parents, "dependencies": sorted(dependencies, key=_json),
            "raw_rows": sorted(_json(_freeze(row)) for row in scope.rows)}


@dataclass(frozen=True)
class Snapshot:
    resolved: ResolvedReport
    filters: dict
    output_options: dict
    state: dict
    digest: str


def capture(name, filters, *, output_options=None):
    """Fresh native authorization, complete raw inventory, and source callbacks."""
    resolved = resolve_report(name)
    if resolved is None:
        return None
    effective = _mapping(filters)
    options = _mapping(output_options)
    if validator := resolved.spec.get("validate_filters"):
        validator(copy.deepcopy(effective))
    scope = resolved.spec["selector"](copy.deepcopy(effective))
    _require(isinstance(scope, reports.ReportScope))
    before = _inventory(scope)
    # Reuse the actual source guard against the exact selected raw scope. This
    # does not monkeypatch it, grant recursive completion, or repeat native SQL.
    spec = {**resolved.spec, "selector": lambda _filters: scope}
    native = reports._safe_capture(spec, copy.deepcopy(effective), _actor())
    _require(before == _inventory(scope))
    again = resolve_report(name)
    _require(again is not None and again.chain == resolved.chain)
    state = {"version": VERSION, "requested_report": resolved.requested_name,
             "canonical_report": resolved.canonical_name, "definition": resolved.chain,
             "filters": effective, "output_options": options,
             "inventory": before, "source_capture_sha256": _digest(native)}
    return Snapshot(resolved, effective, options, state, _digest(state))


def _prepared_binding(name):
    if name is None:
        return None
    doc = frappe.get_doc("Prepared Report", name)
    return {"name": doc.name, "report_name": doc.get("report_name"),
            "owner": doc.get("owner"), "filters": _mapping(doc.get("filters"))}


def _prepared_consumer_read(name, binding):
    """Keep native PR roles/UP without recursively invoking B's source gate."""
    from jifeng_erp.sample_source_prepared_reports import _native_permission

    doc = frappe.get_doc("Prepared Report", name)
    _require(doc.doctype == "Prepared Report" and doc.name == name
             and _prepared_binding(name) == binding)
    shared = {"share_doctype": "Prepared Report", "share_name": name}
    _require(not frappe.db.exists("DocShare", shared))
    _require(_native_permission(doc, "read", _actor()))
    _require(not frappe.db.exists("DocShare", shared)
             and _prepared_binding(name) == binding
             and _freeze(frappe.get_doc("Prepared Report", name)) == _freeze(doc))


@dataclass
class Generation:
    requested_name: str
    filters: dict
    output_options: dict
    artifact_kind: str
    generation_id: str
    user: str
    snapshot: Snapshot
    prepared_report: str | None
    source_artifact: str | None
    prepared_binding: dict | None
    file_doc: object = None
    sealed_artifact: object = None
    observations: dict = field(default_factory=dict)
    _inputs: str = ""
    _file_expected: dict | None = None
    _payload: bytes | None = None
    _content: bytes | None = None
    _record_doc: object = None
    _record_expected: dict | None = None
    _parent: dict | None = None

    @property
    def resolved(self):
        return self.snapshot.resolved


def current_frame():
    return _FRAME.get()


current_generation = current_frame


def _live(frame, *, allow_sealed=False):
    _require(isinstance(frame, Generation) and current_frame() is frame
             and frame.user == _actor() and (allow_sealed or frame.sealed_artifact is None))
    _require(_digest({"name": frame.requested_name, "filters": frame.filters,
                      "options": frame.output_options, "kind": frame.artifact_kind,
                      "id": frame.generation_id, "prepared": frame.prepared_report,
                      "source": frame.source_artifact}) == frame._inputs)


@contextmanager
def generation(name, filters, *, prepared_report=None, artifact_kind="JSON",
               source_artifact=None, output_options=None):
    _require(current_frame() is None and artifact_kind in _KINDS)
    snapshot = capture(name, filters, output_options=output_options)
    _require(snapshot is not None)
    binding = _prepared_binding(prepared_report)
    user = _actor()
    if binding is not None:
        _require(binding["report_name"] == name and binding["filters"] == snapshot.filters)
        if artifact_kind != "JSON":
            _prepared_consumer_read(prepared_report, binding)
    if artifact_kind == "JSON":
        _require(binding is not None and binding["owner"] == user and source_artifact is None)
    if artifact_kind == "CSV":
        _require(binding is not None and isinstance(source_artifact, str) and source_artifact)
    if artifact_kind == "Execution":
        _require(prepared_report is None)
    parent = (_parent_binding(source_artifact, snapshot.resolved.canonical_name,
                              artifact_kind, prepared_report) if source_artifact else None)
    frame = Generation(name, copy.deepcopy(snapshot.filters), copy.deepcopy(snapshot.output_options),
                       artifact_kind, str(uuid.uuid4()), user, snapshot,
                       prepared_report, source_artifact, binding)
    frame._parent = parent
    frame._inputs = _digest({"name": name, "filters": frame.filters, "options": frame.output_options,
                            "kind": artifact_kind, "id": frame.generation_id,
                            "prepared": prepared_report, "source": source_artifact})
    token = _FRAME.set(frame)
    try:
        yield frame
        _fresh_frame(frame, allow_sealed=True)
        if frame.file_doc is not None:
            _require(frame.sealed_artifact is not None)
            check_artifact(frame.sealed_artifact.name, file_doc=frame.file_doc, content=frame._content)
    finally:
        _FRAME.reset(token)


def _extra(doctype, fieldname, names):
    """Exact native custom-field query plus raw requested identities/whole gates."""
    from jifeng_erp import sample_source_queries as queries

    _require(isinstance(doctype, str) and doctype and isinstance(fieldname, str) and fieldname)
    _require(isinstance(names, (list, tuple, set)) and names
             and all(isinstance(name, str) and name for name in names))
    names = sorted(set(names))
    # Match get_data_for_custom_field: datatype-level native read, then get_list
    # with requested IDs, preserving UP, omitted names, and native field handling.
    _require(frappe.has_permission(doctype, "read", user=_actor()))
    parents, fields, dependencies = [], {}, []
    states = [_state(doctype, name) for name in names]
    if doctype in queries.COVERED:
        for name in names:
            doc = reports.sources._reference(doctype, name)
            if doc is None:
                continue  # native ordinary missing/UP-omitted names stay omitted
            if doctype in reports.sources.PARENTS:
                parent = doc
                fields[doctype] = (fieldname,)
            else:
                # Ordinary children retain native get_list behavior. An exact
                # protected parent must validate the raw child/table identity.
                parent = (reports.sources._reference(doc.get("parenttype"), doc.get("parent"))
                          if doc.get("parenttype") in reports.sources.PARENTS else None)
                if parent is None or not reports.sources._scope(parent):
                    continue
                parent = reports.sources._child_parent(doc)
                dependencies.append(reports.ReportDependency(
                    owner=(parent.doctype, parent.name), doctype=doctype, name=name,
                    fields=(fieldname,), parenttype=parent.doctype,
                    bound_child=(parent.doctype, parent.name, doc.get("parentfield")),
                ))
            parents.append((parent.doctype, parent.name))
    scope = reports.ReportScope(rows=[{"doctype": doctype, "name": name} for name in names],
                               parents=list(dict.fromkeys(parents)), fields=fields,
                               dependencies=dependencies)
    native = reports._safe_capture({"selector": lambda _filters: scope}, {}, _actor())
    data = frappe._dict(frappe.get_list(doctype, filters={"name": ["in", names]},
                                      fields=["name", fieldname], as_list=1))
    _require(states == [_state(doctype, name) for name in names])
    return data, {"doctype": doctype, "field": fieldname, "names": names,
                  "states": states, "data_sha256": _digest(data),
                  "source_capture_sha256": _digest(native)}


def record_extra_dependency(frame, *, doctype, field, names):
    _live(frame)
    data, observation = _extra(doctype, field, names)
    key = _json([doctype, field, observation["names"]])
    previous = frame.observations.get(key)
    _require(previous is None or previous == observation)
    frame.observations[key] = observation
    return data


def _check_observations(observations):
    for observation in observations:
        _data, current = _extra(observation["doctype"], observation["field"], observation["names"])
        _require(current == observation)


def new_pending_file(frame, *, payload, content, file_name):
    _live(frame)
    _require(frame.artifact_kind != "Execution" and frame.file_doc is None)
    _bytes_digest(payload)
    _bytes_digest(content)
    _require(isinstance(file_name, str) and file_name and "\x00" not in file_name
             and file_name not in {".", ".."})
    if frame.artifact_kind == "JSON":
        _require(file_name.endswith(".json.gz") and gzip.decompress(content) == payload)
    target = ("Prepared Report", frame.prepared_report) if frame.prepared_report else (
        "Report", frame.requested_name)
    # The exact native File.save_file_on_filesystem filename transformation.
    # Apply it before registration so legal Custom Report names containing
    # percent/query/fragment characters retain one immutable UUID target.
    fixed_name = frame.generation_id + "_" + re.sub(r"[/\\%?#]", "_", file_name)
    document = frappe.get_doc({"doctype": "File", "file_name": fixed_name,
                               "attached_to_doctype": target[0], "attached_to_name": target[1],
                               "content": content, "is_private": 1, "owner": frame.user,
                               **({"folder": "Home/Exported Reports"} if frame.artifact_kind == "Export" else {})})
    frame.file_doc, frame._payload, frame._content = document, payload, content
    frame._file_expected = {"file_name": fixed_name, "attached_to_doctype": target[0],
                            "attached_to_name": target[1], "attached_to_field": None,
                            "is_private": 1, "is_folder": 0, "owner": frame.user}
    if frame.artifact_kind == "Export":
        frame._file_expected["folder"] = "Home/Exported Reports"
    return document


def authorize_file_write(doc):
    frame = current_frame()
    if (frame is None or frame.file_doc is not doc or frame.artifact_kind == "Execution"
            or frame.sealed_artifact is not None):
        return False
    _live(frame)
    for key, expected in frame._file_expected.items():
        actual = doc.get(key)
        if key in {"is_private", "is_folder"}:
            actual = int(actual or 0)
        if key == "attached_to_field":
            actual = actual or None
        _require(actual == expected)
    _require(doc.doctype == "File" and doc.get("content") == frame._content)
    if doc.get("file_url"):
        _require(doc.get("file_url") == "/private/files/" + frame._file_expected["file_name"])
    return True


def _file_identity(doc):
    from jifeng_erp.sample_source_report_files import canonical_file_identity

    identity = canonical_file_identity(doc)
    _require(isinstance(identity, dict) and identity.get("private") and isinstance(identity.get("path"), str)
             and isinstance(identity.get("path_sha256"), str))
    return identity


def _file_state(doc):
    return {key: doc.get(key) for key in _FILE_FIELDS}


def _disk_bytes(identity):
    path = Path(identity["path"])
    _require(path.is_file() and not path.is_symlink())
    return path.read_bytes()


def _fresh_frame(frame, *, allow_sealed=False):
    _live(frame, allow_sealed=allow_sealed)
    _require(_prepared_binding(frame.prepared_report) == frame.prepared_binding)
    if frame.prepared_binding is not None and frame.artifact_kind != "JSON":
        _prepared_consumer_read(frame.prepared_report, frame.prepared_binding)
    current = capture(frame.requested_name, frame.filters, output_options=frame.output_options)
    _require(current is not None and current.digest == frame.snapshot.digest)
    _check_observations(frame.observations.values())
    parent = (_parent_binding(frame.source_artifact, frame.resolved.canonical_name,
                              frame.artifact_kind, frame.prepared_report)
              if frame.source_artifact else None)
    _require(parent == frame._parent)
    if frame.prepared_binding is not None and frame.artifact_kind != "JSON":
        _prepared_consumer_read(frame.prepared_report, frame.prepared_binding)


def seal(frame, file_doc):
    """Seal once after actual private File bytes and final source rechecks."""
    _live(frame)
    _require(authorize_file_write(file_doc) and file_doc.get("name"))
    _fresh_frame(frame)
    actual = frappe.get_doc("File", file_doc.name)
    _require(_file_state(actual) == _file_state(file_doc))
    identity = _file_identity(actual)
    content = _disk_bytes(identity)
    _require(content == frame._content)
    record = {"name": frame.generation_id, "state": "Sealed", "artifact_kind": frame.artifact_kind,
              "report": frame.requested_name, "prepared_report": frame.prepared_report,
              "file": actual.name, "source_artifact": frame.source_artifact,
              "canonical_path_sha256": identity["path_sha256"], "owner": frame.user}
    receipt = {"version": VERSION, "generation_id": frame.generation_id, "actor": frame.user,
               "record": record, "source_parent": frame._parent,
               "snapshot": frame.snapshot.state, "snapshot_sha256": frame.snapshot.digest,
               "prepared_binding": frame.prepared_binding,
               "observations": sorted(frame.observations.values(), key=_json),
               "payload_sha256": _bytes_digest(frame._payload),
               "content_sha256": _bytes_digest(content), "file": _file_state(actual),
               "canonical_path_sha256": identity["path_sha256"]}
    values = {**record, "receipt": _json(receipt)}
    doc = frappe.get_doc({"doctype": DOCTYPE, **values})
    frame._record_doc, frame._record_expected = doc, values
    doc.insert(ignore_permissions=True, set_name=frame.generation_id)
    validate_record_write(doc)
    _fresh_frame(frame)
    _require(identity == _file_identity(frappe.get_doc("File", actual.name))
             and content == _disk_bytes(identity))
    frame.sealed_artifact = doc
    # Native content/decode are virtual input state. Later reads of this same
    # object must use the sealed disk path, never the former upload shortcut.
    file_doc.content, file_doc.decode = None, False
    return doc


def can_write_record(doc, permtype="write", user=None):
    frame = current_frame()
    return bool(frame and frame._record_doc is doc and frame.sealed_artifact is None
                and (user is None or user == frame.user) and frame.user == frappe.session.user
                and permtype == "create")


def validate_record_write(doc):
    frame = current_frame()
    _require(frame and frame._record_doc is doc and frame.sealed_artifact is None)
    _live(frame)
    _require(doc.doctype == DOCTYPE and all(doc.get(key) == expected
                                          for key, expected in frame._record_expected.items()))
    _require(doc.get("owner") in (None, frame.user))


def prevent_record_change(*args, **kwargs):
    _deny()


def artifact_has_permission(doc, ptype=None, user=None, **kwargs):
    return can_write_record(doc, ptype or "read", user)


def artifact_query_conditions(user=None):
    return "1=0"


def before_validate_artifact_share(doc, method=None):
    if doc.get("share_doctype") == DOCTYPE:
        _deny()


@dataclass(frozen=True)
class ArtifactProof:
    artifact: object
    file_doc: object
    receipt: dict
    file_name: str
    report_name: str
    prepared_report: str | None
    content_sha256: str
    payload_sha256: str
    content_verified: bool
    content: bytes | None = None
    payload: bytes | None = None

    @property
    def name(self):
        return self.artifact.name


def _sealed(name):
    try:
        doc = frappe.get_doc(DOCTYPE, name)
    except frappe.DoesNotExistError:
        _deny(LEGACY_MESSAGE)
    _require(doc.doctype == DOCTYPE and doc.get("state") == "Sealed"
             and doc.get("artifact_kind") in _KINDS - {"Execution"})
    try:
        receipt = json.loads(doc.get("receipt"))
    except (ValueError, TypeError):
        _deny()
    _require(isinstance(receipt, dict) and receipt.get("version") == VERSION
             and receipt.get("generation_id") == doc.name
             and receipt.get("canonical_path_sha256") == doc.get("canonical_path_sha256")
             and receipt.get("file", {}).get("name") == doc.get("file")
             and receipt.get("snapshot", {}).get("requested_report") == doc.get("report"))
    _require(isinstance(receipt.get("record"), dict)
             and receipt["record"] == {key: doc.get(key) for key in receipt["record"]}
             and set(receipt["record"]) == (set(_RECORD_FIELDS) - {"receipt"} | {"owner"})
             and doc.get("owner") == receipt.get("actor")
             and isinstance(receipt.get("snapshot"), dict)
             and isinstance(receipt["snapshot"].get("filters"), dict)
             and isinstance(receipt["snapshot"].get("output_options"), dict))
    if doc.artifact_kind == "JSON":
        _require(doc.get("prepared_report") and not doc.get("source_artifact")
                 and receipt.get("source_parent") is None)
    if doc.artifact_kind == "CSV":
        _require(doc.get("prepared_report") and doc.get("source_artifact"))
    return doc, receipt


def _parent_binding(name, canonical_name, artifact_kind, prepared_report):
    """Only a fixed server-selected JSON can be a derived output's parent."""
    parent, receipt = _sealed(name)
    _require(parent.artifact_kind == "JSON"
             and receipt["snapshot"].get("canonical_report") == canonical_name)
    if artifact_kind == "CSV":
        _require(parent.get("prepared_report") == prepared_report)
    _require(artifact_kind in {"CSV", "Export", "Execution"})
    root = frappe.get_doc("File", parent.file)
    raw = _disk_bytes(_file_identity(root))
    from jifeng_erp.sample_source_report_files import check_parent_file

    stamp = check_parent_file(root)
    _require(stamp.content == raw and stamp.path == _file_identity(root)["path"])
    proof = check_artifact(name, content=raw)
    _require(proof.content_verified and check_parent_file(root).signature == stamp.signature)
    return {"name": name, "receipt_sha256": _bytes_digest(parent.receipt.encode("utf-8")),
            "content_sha256": proof.content_sha256, "payload_sha256": proof.payload_sha256,
            "canonical_report": canonical_name, "canonical_path_sha256": parent.canonical_path_sha256}


def check_artifact(name, *, file_doc=None, content=None):
    """Fresh consumer proof; content=None is permission/metadata precheck only."""
    _require(isinstance(name, str) and name and name not in _CONSUMING.get())
    token = _CONSUMING.set(_CONSUMING.get() | {name})
    try:
        return _check_artifact(name, file_doc=file_doc, content=content)
    except Exception as error:
        _deny(LEGACY_MESSAGE if str(error) == LEGACY_MESSAGE else _DENIED)
    finally:
        _CONSUMING.reset(token)


def _check_artifact(name, *, file_doc=None, content=None):
    _actor()
    doc, receipt = _sealed(name)
    snapshot = receipt["snapshot"]
    _require(_digest(snapshot) == receipt.get("snapshot_sha256"))
    current = capture(doc.report, snapshot["filters"], output_options=snapshot["output_options"])
    _require(current is not None and current.digest == receipt["snapshot_sha256"])
    _require(_prepared_binding(doc.get("prepared_report")) == receipt.get("prepared_binding"))
    _check_observations(receipt.get("observations") or [])
    root = frappe.get_doc("File", doc.file)
    _require(_file_state(root) == receipt["file"])
    identity = _file_identity(root)
    _require(identity["path_sha256"] == receipt["canonical_path_sha256"])
    if file_doc is not None:
        _require(_file_identity(file_doc)["path_sha256"] == identity["path_sha256"])
    payload = None
    if content is not None:
        _require(_bytes_digest(content) == receipt["content_sha256"])
        if doc.artifact_kind == "JSON":
            try:
                payload = gzip.decompress(content)
            except (ValueError, OSError, EOFError):
                _deny()
            _require(_bytes_digest(payload) == receipt["payload_sha256"])
    if doc.get("source_artifact"):
        _require(doc.source_artifact != doc.name)
        parent = _parent_binding(doc.source_artifact, current.resolved.canonical_name,
                                 doc.artifact_kind, doc.get("prepared_report"))
        _require(parent == receipt.get("source_parent"))
    else:
        _require(receipt.get("source_parent") is None)
    after = capture(doc.report, snapshot["filters"], output_options=snapshot["output_options"])
    _require(after is not None and after.digest == current.digest
             and _prepared_binding(doc.get("prepared_report")) == receipt.get("prepared_binding")
             and _file_state(frappe.get_doc("File", root.name)) == receipt["file"]
             and _file_identity(frappe.get_doc("File", root.name)) == identity)
    _check_observations(receipt.get("observations") or [])
    if doc.get("source_artifact"):
        _require(_parent_binding(doc.source_artifact, after.resolved.canonical_name,
                                 doc.artifact_kind, doc.get("prepared_report")) == receipt["source_parent"])
    _require(_freeze(frappe.get_doc(DOCTYPE, doc.name)) == _freeze(doc))
    return ArtifactProof(doc, root, receipt, root.file_name, doc.report, doc.get("prepared_report"),
                         receipt["content_sha256"], receipt["payload_sha256"], content is not None,
                         content, payload)


def artifacts_for_prepared(name):
    return frappe.get_all(DOCTYPE, filters={"prepared_report": name}, pluck="name", order_by="name asc")


def bound_roots(file_doc):
    """Union current/persisted file and physical-path indexed roots; no recursion."""
    documents = [file_doc]
    if file_doc.get("name") and frappe.db.exists("File", file_doc.name):
        documents.append(frappe.get_doc("File", file_doc.name))
    roots = set()
    for doc in documents:
        if doc.get("name"):
            roots.update(frappe.get_all(DOCTYPE, filters={"file": doc.name}, pluck="name"))
        if doc.get("file_url"):
            from jifeng_erp.sample_source_report_files import canonical_file_identity

            identity = canonical_file_identity(doc)
            if identity is not None:
                roots.update(frappe.get_all(DOCTYPE, filters={"canonical_path_sha256": identity["path_sha256"]},
                                           pluck="name"))
    return sorted(roots)
