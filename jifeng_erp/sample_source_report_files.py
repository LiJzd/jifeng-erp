"""Final File gates for the exact source-aware native report artifacts.

File owners, shares and ``fid`` are not provenance.  A local path is checked
against every server receipt and every File alias before and after consumption.
Independent ordinary files retain the native implementation and permissions.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from urllib.parse import unquote

import frappe


_DENIED = "报表文件来源不可读或已变化，请按原筛选重新生成"
_LEGACY = "旧快照缺少来源校验，请按原筛选重新生成"
_SHARE_DENIED = "报表文件必须按原始业务来源读取，不能单独分享"
_FILE_FIELDS = (
    "name", "file_name", "file_url", "is_private", "is_folder", "owner",
    "attached_to_doctype", "attached_to_name", "attached_to_field",
    "content_hash", "file_size", "modified",
)
_BIND_FIELDS = (
    "name", "file_name", "file_url", "is_private", "is_folder", "owner",
    "attached_to_doctype", "attached_to_name", "attached_to_field",
)
_REQUEST_READS = "_jifeng_source_report_file_reads"


def _artifacts():
    # A and C deliberately use lazy imports: A owns the trust root, C owns the
    # one native physical-path implementation.
    from . import sample_source_report_artifacts
    return sample_source_report_artifacts


def _native():
    from frappe.core.doctype.file import file
    return file


def _deny(message=_DENIED):
    frappe.throw(message, frappe.PermissionError)


def _value(doc, field):
    return doc.get(field)


def _private(doc):
    return _value(doc, "is_private") in (1, "1", True)


def _record(doc):
    return {field: _value(doc, field) for field in _FILE_FIELDS}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, default=str).encode()).hexdigest()


def _persisted(doc):
    name = _value(doc, "name")
    if name and frappe.db.exists("File", name):
        return frappe.get_doc("File", name)
    return None


def _canonical(doc):
    if _value(doc, "is_folder") or not (_value(doc, "file_url") or _value(doc, "file_name")):
        return None
    native = _native()
    # Invoke the native controller's method, not a caller supplied instance
    # attribute. Its same-site URL and file-name rules remain authoritative.
    path = native.File.get_full_path(doc)
    if path.startswith(native.URL_PREFIXES):
        return None
    if ".." in str(_value(doc, "file_url") or "").split("/"):
        _deny()
    real = os.path.normcase(os.path.realpath(path))
    private_root = os.path.normcase(os.path.realpath(frappe.get_site_path("private", "files")))
    public_root = os.path.normcase(os.path.realpath(frappe.get_site_path("public", "files")))
    roots = ((private_root, True), (public_root, False))
    for root, private in roots:
        if os.path.commonpath((root, real)) == root and real != root:
            logical_private = os.path.commonpath((
                os.path.abspath(frappe.get_site_path("private", "files")),
                os.path.abspath(path),
            )) == os.path.abspath(frappe.get_site_path("private", "files"))
            return {"path": real, "path_sha256": hashlib.sha256(real.encode()).hexdigest(),
                    "private": private and logical_private}
    _deny()


def _file_rows():
    # A different URL, missing/falsified content_hash or symlink can refer to the
    # same physical file. Neither a URL query nor a hash query is a full inventory.
    return frappe.get_all("File", fields=list(_FILE_FIELDS), limit_page_length=0)


def _alias_documents(path):
    aliases = []
    for row in _file_rows():
        candidate = frappe.get_doc(doctype="File", **dict(row))
        try:
            identity = _canonical(candidate)
        except Exception:
            # Unrelated malformed ordinary File rows do not deny a valid path.
            # A receipt's exact File identity is separately checked by A.
            continue
        if identity and identity["path"] == path:
            aliases.append(candidate)
    return sorted(aliases, key=lambda doc: str(_value(doc, "name") or ""))


def canonical_file_identity(doc):
    """Server-only canonical identity shared with the receipt service.

    ``aliases`` contains persisted identities, including aliases that carry a
    different parent or hash. This function performs no source authorization.
    """
    identity = _canonical(doc)
    if identity is None:
        return None
    return {**identity, "aliases": [_record(alias) for alias in _alias_documents(identity["path"])]}


def _legacy_scoped(doc):
    if _value(doc, "is_folder"):
        return False
    doctype = _value(doc, "attached_to_doctype")
    name = _value(doc, "attached_to_name")
    if not name:
        return False
    if doctype == "Prepared Report":
        report = frappe.db.get_value("Prepared Report", name, "report_name")
        return bool(report and _artifacts().resolve_report(report) is not None)
    if doctype == "Report":
        return _artifacts().resolve_report(name) is not None
    return False


def _raw_bytes(path):
    # Read bytes independently: native get_content may return decoded text or
    # a caller populated content field. The sealed artifact binds disk bytes.
    with open(path, "rb") as stream:
        return stream.read()


@dataclass(frozen=True)
class _ReadStamp:
    signature: str
    path: str
    content: bytes
    user: str


def _capture(doc):
    service = _artifacts()
    persisted = _persisted(doc)
    subjects = [doc] + ([persisted] if persisted is not None else [])
    identities = []
    for subject in subjects:
        identity = _canonical(subject)
        if identity:
            identities.append(identity)
    paths = {identity["path"] for identity in identities}
    aliases = []
    for path in sorted(paths):
        aliases.extend(_alias_documents(path))
    from .ledger_report_cache import check_file_subjects
    check_file_subjects([*subjects, *aliases])
    roots = set()
    for subject in [*subjects, *aliases]:
        roots.update(service.bound_roots(subject))
    legacy = any(_legacy_scoped(subject) and not service.bound_roots(subject)
                 for subject in [*subjects, *aliases])
    if legacy:
        _deny(_LEGACY)
    if not roots:
        return None
    if len(paths) != 1 or not identities or any(not identity["private"] for identity in identities):
        _deny()
    if any(not _canonical(alias)["private"] for alias in aliases):
        _deny()
    if any(not _private(subject) for subject in [*subjects, *aliases]):
        _deny()
    names = sorted({str(_value(subject, "name")) for subject in [*subjects, *aliases]
                    if _value(subject, "name")})
    if names and frappe.get_all("DocShare", filters={"share_doctype": "File",
                                                      "share_name": ["in", names]},
                                 fields=["name"], limit_page_length=0):
        _deny(_SHARE_DENIED)
    if persisted is not None and any(_value(doc, field) != _value(persisted, field)
                                     for field in _BIND_FIELDS):
        _deny()
    path = next(iter(paths))
    raw = _raw_bytes(path)
    supplied = _value(doc, "content")
    if supplied:
        if persisted is not None:
            _deny()
        if _value(doc, "decode"):
            from frappe.core.doctype.file.utils import decode_file_content
            supplied = decode_file_content(supplied)
        if isinstance(supplied, str):
            supplied = supplied.encode()
        if supplied != raw:
            _deny()
    proofs = []
    for name in sorted(roots):
        proof = service.check_artifact(name, content=raw)
        if not proof.content_verified:
            _deny()
        proofs.append({"name": proof.name, "receipt": proof.receipt})
    current = _canonical(doc)
    if not current or current["path"] != path:
        _deny()
    inventory = {"current": _record(doc), "persisted": _record(persisted) if persisted else None,
                 "paths": identities, "aliases": [_record(alias) for alias in aliases],
                 "roots": proofs, "bytes": hashlib.sha256(raw).hexdigest()}
    # Receipt lineage freezes file/source state. The consumer belongs to this
    # particular read and must not become a permanent recipient restriction.
    return _ReadStamp(_digest(inventory), path, raw, frappe.session.user)


def _checked_capture(doc):
    try:
        return _capture(doc)
    except frappe.PermissionError as error:
        _deny(_LEGACY if str(error) == _LEGACY else _DENIED)
    except Exception:
        _deny()


def _stable(doc, before):
    after = _checked_capture(doc)
    if ((before is None) != (after is None)
            or (before and (before.signature != after.signature or before.user != after.user))):
        _deny()
    return after


def _register_read(doc, stamp):
    if stamp is None or getattr(frappe.local, "request", None) is None:
        return
    reads = getattr(frappe.local, _REQUEST_READS, None)
    if reads is None:
        reads = []
        setattr(frappe.local, _REQUEST_READS, reads)
    reads.append((doc, stamp))


def _generation(doc):
    # A checks object identity, fixed frame kind/user, exact private binding,
    # server filename and original bytes. No File.flags value can grant this.
    service = _artifacts()
    allowed = bool(service.authorize_file_write(doc))
    if allowed and (not isinstance(_value(doc, "content"), bytes) or _value(doc, "decode")):
        _deny()
    frame = service.current_generation()
    if (not allowed and frame is not None and getattr(frame, "file_doc", None) is doc
            and getattr(frame, "sealed_artifact", None) is None):
        _deny()
    return allowed


def check_parent_file(doc):
    """A's derived-artifact consumer uses this same physical-path union gate."""
    stamp = _checked_capture(doc)
    if stamp is None:
        _deny()
    return stamp


def _mutation(doc, *, writing=False):
    if _generation(doc):
        return True
    stamp = _checked_capture(doc)
    if stamp and writing:
        _deny()
    return False


def _dedup_candidate(doc, content=None, *, assignment=False):
    native = _native()
    if assignment:
        if not _value(doc, "content_hash"):
            # Native generate_content_hash reads the basename from the private
            # or public root, rather than get_full_path. Guard that actual read.
            if _value(doc, "file_url") and not doc.is_remote_file:
                private = bool(_value(doc, "is_private"))
                basename = _value(doc, "file_url").split("/")[-1]
                subject = frappe.get_doc(doctype="File", file_name=basename,
                                         file_url=("/private/files/" if private else "/files/") + basename,
                                         is_private=int(private))
                before = _checked_capture(subject)
                native.File.generate_content_hash(doc)
                _stable(subject, before)
            else:
                native.File.generate_content_hash(doc)
        filters = {"content_hash": _value(doc, "content_hash"),
                   "is_private": _value(doc, "is_private")}
        if _value(doc, "name"):
            filters["name"] = ("!=", _value(doc, "name"))
        if _value(doc, "attached_to_doctype") and _value(doc, "attached_to_name"):
            filters.update({"attached_to_doctype": _value(doc, "attached_to_doctype"),
                            "attached_to_name": _value(doc, "attached_to_name")})
        candidate = frappe.db.get_value("File", filters, ["name", "file_url"], as_dict=1)
    else:
        if content is None:
            content = getattr(doc, "_content", None) or _value(doc, "content")
        if not content:
            return
        if _value(doc, "decode"):
            from frappe.core.doctype.file.utils import decode_file_content
            content = decode_file_content(content)
        filters = {"content_hash": native.get_content_hash(content),
                   "is_private": native.cint(_value(doc, "is_private"))}
        candidate = frappe.get_value("File", filters, ["file_url", "name"], as_dict=True)
    if candidate:
        # Equal bytes in a different physical file are not a source dependency.
        # Check the actual native-selected candidate, then the final actual URL.
        _checked_capture(frappe.get_doc("File", candidate.name))


class SampleSourceReportFileMixin:
    def on_trash(self):
        # The native method unlinks bytes before doc_events execute.
        from .ledger_report_cache import prevent_file_delete
        prevent_file_delete(self)
        return super().on_trash()

    def has_permission(self, permtype="read", *, debug=False, user=None):
        if user and user != frappe.session.user:
            # Source permissions must be evaluated under the native effective
            # consumer; C does not install an arbitrary impersonation context.
            try:
                if _checked_capture(self) is not None:
                    return False
            except frappe.PermissionError:
                return False
        try:
            if _generation(self):
                return super().has_permission(permtype, debug=debug, user=user)
            before = _checked_capture(self)
            if before and permtype in {"share", "delete"}:
                return False
            allowed = super().has_permission(permtype, debug=debug, user=user)
            _stable(self, before)
            return allowed
        except frappe.PermissionError:
            return False

    def is_downloadable(self):
        try:
            before = _checked_capture(self)
            allowed = super().is_downloadable()
            _stable(self, before)
            if allowed:
                _register_read(self, before)
            return allowed
        except frappe.PermissionError:
            return False

    def get_content(self, encodings=None):
        if _generation(self):
            result = super().get_content(encodings=encodings)
            if not _generation(self):
                _deny()
            return result
        before = _checked_capture(self)
        if before is None:
            result = super().get_content(encodings=encodings)
            _stable(self, before)
            return result
        native = _native()
        options = native.FILE_ENCODING_OPTIONS if encodings is None else tuple(encodings)
        supplied = _value(self, "content")
        decode = _value(self, "decode")
        result = super().get_content(encodings=options)
        _stable(self, before)
        expected = supplied if supplied else before.content
        if supplied and decode:
            from frappe.core.doctype.file.utils import decode_file_content
            expected = decode_file_content(supplied)
        elif not supplied and not expected.startswith(native.OLE_FILE_SIGNATURE):
            for encoding in options:
                try:
                    expected = before.content.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
        if type(result) is not type(expected) or result != expected:
            _deny()
        _register_read(self, before)
        return result

    def before_insert(self):
        if _generation(self):
            _mutation(self)
        else:
            url = _value(self, "file_url") or ""
            normalized = unquote(url)
            if normalized != url:
                # Native before_insert decodes exactly once before any write.
                # Check that future identity without mutating the caller or
                # double-decoding it before the native method executes.
                candidate = frappe.get_doc(doctype="File", **{
                    **_record(self), "file_url": normalized,
                    "content": _value(self, "content"), "decode": _value(self, "decode"),
                })
                _mutation(candidate)
            else:
                _mutation(self)
        result = super().before_insert()
        if not _generation(self):
            _checked_capture(self)
        return result

    def before_validate(self):
        _mutation(self)
        parent = getattr(super(), "before_validate", None)
        return parent() if parent else None

    def handle_is_private_changed(self):
        _mutation(self)
        return super().handle_is_private_changed()

    def save_file(self, content=None, decode=False, ignore_existing_file_check=False, overwrite=False):
        if _generation(self):
            # The fixed server filename is retained, without hash reuse. write_file
            # uses exclusive creation, so overwrite=True cannot replace a path.
            if (content is not None and content != _value(self, "content")) or decode:
                _deny()
            result = super().save_file(content=content, decode=False,
                                       ignore_existing_file_check=True, overwrite=True)
            if not _generation(self):
                _deny()
            return result
        _mutation(self, writing=True)
        if not ignore_existing_file_check:
            _dedup_candidate(self, content)
        result = super().save_file(content=content, decode=decode,
                                  ignore_existing_file_check=ignore_existing_file_check,
                                  overwrite=overwrite)
        _checked_capture(self)
        return result

    def validate_duplicate_entry(self):
        if _generation(self):
            return None
        _checked_capture(self)
        if not self.flags.ignore_duplicate_entry_error and not _value(self, "is_folder"):
            _dedup_candidate(self, assignment=True)
        result = super().validate_duplicate_entry()
        _checked_capture(self)
        return result

    def write_file(self):
        if not _generation(self):
            _mutation(self, writing=True)
            return super().write_file()
        identity = _canonical(self)
        if not identity or not identity["private"] or _value(self, "is_private") != 1:
            _deny()
        if _alias_documents(identity["path"]):
            _deny()
        # A pre-existing logical UUID entry, including a dangling symlink, is
        # not a new server-owned target even if its real target does not exist.
        if os.path.lexists(_native().File.get_full_path(self)):
            _deny()
        self.check_content()
        if not _generation(self):
            _deny()
        raw = getattr(self, "_content", None)
        if not isinstance(raw, bytes) or raw != _value(self, "content"):
            _deny()
        try:
            with open(identity["path"], "xb") as stream:
                stat = os.fstat(stream.fileno())
                # Native cleanup decides sharing by MD5, which can equal an
                # unrelated ordinary file even though this path is exclusive.
                # Bind cleanup to the actual newly created inode instead.
                path = identity["path"]

                def rollback_exclusive_file():
                    try:
                        try:
                            current = os.stat(path, follow_symlinks=False)
                        except FileNotFoundError:
                            current = None
                        if current and (current.st_dev, current.st_ino) == (stat.st_dev, stat.st_ino):
                            os.unlink(path)
                    finally:
                        # Later native callbacks must neither delete a replaced
                        # inode nor restore original_content into this new path.
                        for flag in ("new_file", "original_content", "original_path"):
                            self.flags.pop(flag, None)

                self.flags.new_file = True
                frappe.db.after_rollback.add(rollback_exclusive_file)
                frappe.db.after_rollback.add(self.on_rollback)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            _deny()
        if not _generation(self) or _raw_bytes(identity["path"]) != raw:
            _deny()
        return identity["path"]

    def validate_share(self, share):
        if _checked_capture(self) is not None:
            _deny(_SHARE_DENIED)
        parent = getattr(super(), "validate_share", None)
        return parent(share) if parent else None


def prevent_report_file_docshare(document, method=None):
    if document.get("share_doctype") == "File":
        doc = frappe.get_doc("File", document.get("share_name"))
        if _checked_capture(doc) is not None:
            _deny(_SHARE_DENIED)


def report_file_query_conditions(user=None, doctype=None):
    user = user or frappe.session.user
    denied = []
    scoped = set()
    for row in _file_rows():
        doc = frappe.get_doc(doctype="File", **dict(row))
        try:
            stamp = _checked_capture(doc)
            if stamp:
                scoped.add(row.name)
                if user != frappe.session.user:
                    denied.append(row.name)
        except frappe.PermissionError:
            denied.append(row.name)
            scoped.add(row.name)
    # Native query builders OR shared names around all PQCs. An invalid scoped
    # share must not silently revive a row; release inventory must prove zero.
    if scoped and frappe.get_all("DocShare", filters={"share_doctype": "File",
                                                      "share_name": ["in", sorted(scoped)]},
                                 fields=["name"], limit_page_length=0):
        _deny(_SHARE_DENIED)
    if not denied:
        return ""
    names = ", ".join(frappe.db.escape(name) for name in sorted(set(denied)))
    return f"`tabFile`.`name` NOT IN ({names})"


def after_report_file_request(response, request=None):
    """Finalize only a successful scoped consumption, including X-Accel reads."""
    reads = getattr(frappe.local, _REQUEST_READS, None)
    if reads is not None:
        delattr(frappe.local, _REQUEST_READS)
    if not reads or not (200 <= response.status_code < 300 or response.status_code == 304):
        return
    try:
        for doc, stamp in reads:
            _stable(doc, stamp)
    except Exception:
        # Frappe logs and swallows exceptions in after_request; mutate the actual
        # response here so a stale X-Accel header cannot still serve the bytes.
        response.direct_passthrough = False
        response.status_code = 403
        response.headers.pop("X-Accel-Redirect", None)
        response.headers.pop("Content-Disposition", None)
        response.headers.pop("Content-Range", None)
        response.headers.pop("ETag", None)
        response.headers.pop("Last-Modified", None)
        response.headers.pop("Accept-Ranges", None)
        response.set_data(_DENIED)
        response.headers["Content-Type"] = "text/plain; charset=utf-8"
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
