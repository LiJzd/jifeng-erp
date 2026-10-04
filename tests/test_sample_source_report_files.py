"""File trust-boundary models; these are not native DB/HTTP/queue acceptance."""

import base64
import copy
import gzip
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote, unquote


ROOT = Path(__file__).parents[1]


class Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, key):
        del self[key]


class NativeFile(Row):
    """Small native-behavior model, with actual path and byte operations."""
    calls = []
    rows = None
    site = None
    read_effect = None

    def __init__(self, **values):
        super().__init__(doctype="File", content=b"", decode=False, is_private=1, is_folder=0,
                         flags=Row())
        self.update(values)

    def get_full_path(self):
        path = self.file_url or self.file_name
        if path.startswith("https://local/"):
            path = path.removeprefix("https://local")
        if path.startswith(("http://", "https://", "/api/method/")):
            return path
        if "/" not in path:
            path = ("/private/files/" if self.is_private else "/files/") + path
        if path.startswith("/private/files/"):
            return str(self.site / "private" / "files" / path.removeprefix("/private/files/"))
        if path.startswith("/files/"):
            return str(self.site / "public" / "files" / path.removeprefix("/files/"))
        return path

    @property
    def is_remote_file(self):
        return bool(self.file_url and self.file_url.startswith(("http://", "https://", "/api/method/")))

    def generate_content_hash(self):
        if self.content_hash or not self.file_url or self.is_remote_file:
            return
        base = self.site / ("private" if self.is_private else "public") / "files"
        self.content_hash = hashlib.md5((base / self.file_url.split("/")[-1]).read_bytes(),
                                       usedforsecurity=False).hexdigest()

    def has_permission(self, permtype="read", *, debug=False, user=None):
        self.calls.append(("permission", permtype, debug, user))
        return self.get("native_allowed", True)

    def is_downloadable(self):
        self.calls.append(("download", self.name))
        return self.get("native_allowed", True)

    def get_content(self, encodings=None):
        self.calls.append(("content", encodings))
        if self.content:
            self._content = base64.b64decode(self.content) if self.decode else self.content
            self.decode = False
            return self._content
        raw = Path(self.get_full_path()).read_bytes()
        if NativeFile.read_effect:
            NativeFile.read_effect(self)
        self._content = raw
        if not raw.startswith(b"OLE!"):
            for encoding in ("utf-8-sig", "utf-8", "windows-1250") if encodings is None else encodings:
                try:
                    self._content = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
        return self._content

    def before_insert(self):
        self.calls.append(("before_insert", self.name))
        self.file_url = unquote(self.file_url or "")
        if self.flags.copy_from_existing_file:
            return "copy"
        self.save_file(content=self.get_content())
        self.flags.new_file = True
        self.validate_duplicate_entry()
        return "insert"

    def handle_is_private_changed(self):
        self.calls.append(("move", self.name))
        return "moved"

    def save_file(self, content=None, decode=False, ignore_existing_file_check=False, overwrite=False):
        self.calls.append(("save", content, decode, ignore_existing_file_check, overwrite))
        if not self.flags.new_file:
            self.flags.original_content = self.get_content()
        if content is not None:
            self.content = content
            self.decode = decode
        self.get_content()
        self.content_hash = hashlib.md5(self._content, usedforsecurity=False).hexdigest()
        if not ignore_existing_file_check:
            for row in self.rows.values():
                if row.content_hash == self.content_hash and row.is_private == self.is_private:
                    self.file_url = row.file_url
                    return "duplicate"
        self.file_url = ("/private/files/" if self.is_private else "/files/") + self.file_name
        return self.write_file()

    def validate_duplicate_entry(self):
        self.calls.append(("duplicate", self.name))
        return "checked"

    def write_file(self):
        self.calls.append(("write", self.name))
        Path(self.get_full_path()).write_bytes(self._content)
        return self.get_full_path()

    def check_content(self):
        self.calls.append(("check_content", self.name))

    def on_rollback(self):
        self.calls.append(("native_rollback", self.name))
        path = Path(NativeFile.get_full_path(self))
        if self.flags.new_file:
            if path.exists():
                path.unlink()
            self.flags.pop("new_file", None)
            self.flags.pop("original_content", None)
            return
        if self.flags.original_content:
            path.write_bytes(self.flags.original_content)
            self.flags.pop("original_content", None)


class Response:
    def __init__(self, code=200):
        self.status_code = code
        self.headers = {"Cache-Control": "native-cache"}
        self.direct_passthrough = True
        self.data = b"native"

    def set_data(self, value):
        self.data = value.encode() if isinstance(value, str) else value


class TestSampleSourceReportFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.site = Path(self.temp.name)
        (self.site / "private" / "files").mkdir(parents=True)
        (self.site / "public" / "files").mkdir(parents=True)
        self.rows = {}
        self.roots = {}
        self.shares = []
        self.prepared = {"PR": "Open Work Orders"}
        self.frame = None
        self.rollback = []
        self.denied_roots = set()
        self.source_versions = {}
        NativeFile.rows = self.rows
        NativeFile.site = self.site
        NativeFile.calls = []
        NativeFile.read_effect = None
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Row(user="actor")
        self.frappe.local = Row(request=None)
        self.frappe.PermissionError = PermissionError
        self.frappe.throw = lambda message, error: (_ for _ in ()).throw(error(message))
        self.frappe.get_site_path = lambda *parts: str(self.site.joinpath(*parts))
        self.frappe.get_all = Mock(side_effect=self.get_all)
        self.frappe.get_value = Mock(side_effect=self.get_value)
        self.frappe.get_doc = self.get_doc
        self.frappe.db = Row(exists=lambda dt, name: name in self.rows,
                             get_value=self.get_value,
                             escape=lambda value: "'" + value.replace("'", "''") + "'",
                             after_rollback=Row(add=self.rollback.append))
        self.native = types.ModuleType("frappe.core.doctype.file.file")
        self.native.File = NativeFile
        self.native.URL_PREFIXES = ("http://", "https://", "/api/method/")
        self.native.FILE_ENCODING_OPTIONS = ("utf-8-sig", "utf-8", "windows-1250")
        self.native.OLE_FILE_SIGNATURE = b"OLE!"
        self.native.get_content_hash = lambda raw: hashlib.md5(
            raw.encode() if isinstance(raw, str) else raw, usedforsecurity=False).hexdigest()
        self.native.cint = lambda value: int(value or 0)
        self.utils = types.ModuleType("frappe.core.doctype.file.utils")
        self.utils.decode_file_content = base64.b64decode
        self.artifacts = types.ModuleType("jifeng_erp.sample_source_report_artifacts")
        self.artifacts.resolve_report = Mock(side_effect=lambda name: Row(name=name)
                                              if name in {"Open Work Orders", "CUSTOM"} else None)
        self.artifacts.bound_roots = Mock(side_effect=self.bound_roots)
        self.artifacts.check_artifact = Mock(side_effect=self.check_artifact)
        self.artifacts.authorize_file_write = Mock(side_effect=self.authorize)
        self.artifacts.current_generation = lambda: self.frame
        self.app = types.ModuleType("jifeng_erp")
        self.app.__path__ = [str(ROOT / "jifeng_erp")]
        self.app.sample_source_report_artifacts = self.artifacts
        file_package = types.ModuleType("frappe.core.doctype.file")
        file_package.file = self.native
        modules = {"frappe": self.frappe, "jifeng_erp": self.app,
                   "jifeng_erp.sample_source_report_artifacts": self.artifacts,
                   "frappe.core.doctype.file": file_package,
                   "frappe.core.doctype.file.file": self.native,
                   "frappe.core.doctype.file.utils": self.utils}
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        name = "jifeng_erp.sample_source_report_files"
        spec = importlib.util.spec_from_file_location(name, ROOT / "jifeng_erp/sample_source_report_files.py")
        self.subject = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.subject
        spec.loader.exec_module(self.subject)
        self.addCleanup(lambda: sys.modules.pop(name, None))
        self.File = type("File", (self.subject.SampleSourceReportFileMixin, NativeFile), {})

    def get_doc(self, *args, **kwargs):
        if args:
            return self.File(**copy.deepcopy(self.rows[args[1]]))
        kwargs.pop("doctype", None)
        return self.File(**copy.deepcopy(kwargs))

    def get_all(self, doctype, filters=None, fields=None, **kwargs):
        if doctype == "DocShare":
            return list(self.shares)
        rows = list(self.rows.values())
        if filters:
            rows = [row for row in rows if all(row.get(key) == value for key, value in filters.items())]
        return [Row({key: row.get(key) for key in fields}) for row in rows]

    def get_value(self, doctype, filters, fields, **kwargs):
        if doctype == "Prepared Report":
            return self.prepared.get(filters)
        for row in self.rows.values():
            if all(row.get(key) != value[1] if isinstance(value, tuple) else row.get(key) == value
                   for key, value in filters.items()):
                return Row({key: row.get(key) for key in fields})
        return None

    def add_file(self, name="F", data=b"amount\n42\n", url=None, parent=None, artifact=None, private=1):
        url = url or ("/private/files/" if private else "/files/") + name + ".csv"
        row = Row(name=name, file_name=url.rsplit("/", 1)[-1], file_url=url, is_private=private,
                  is_folder=0, owner="actor", content_hash=self.native.get_content_hash(data),
                  file_size=len(data), modified="2026-10-04", attached_to_doctype=None,
                  attached_to_name=None, attached_to_field=None)
        if parent:
            row.attached_to_doctype, row.attached_to_name = parent
        self.rows[name] = row
        doc = self.get_doc("File", name)
        Path(NativeFile.get_full_path(doc)).write_bytes(data)
        if artifact:
            self.roots[artifact] = Row(file=name, path=os.path.realpath(NativeFile.get_full_path(doc)), data=data)
            self.source_versions[artifact] = "source-v1"
        return doc

    def bound_roots(self, doc):
        identity = self.subject._canonical(doc)
        return [name for name, root in self.roots.items()
                if doc.name == root.file or identity and identity["path"] == root.path]

    def check_artifact(self, name, *, file_doc=None, content=None):
        if name in self.denied_roots:
            raise PermissionError("secret-source-id")
        root = self.roots[name]
        if content != root.data:
            raise PermissionError("secret-byte-details")
        return Row(name=name, receipt={"source": self.source_versions[name]}, content_verified=content is not None,
                   file_doc=self.get_doc("File", root.file))

    def authorize(self, doc):
        return bool(self.frame and self.frame.doc is doc and doc.is_private == 1
                    and doc.file_name == self.frame.filename and doc.content == self.frame.content
                    and not doc.decode and doc.attached_to_doctype == "Prepared Report"
                    and doc.attached_to_name == "PR")

    def pending(self, name=None):
        doc = self.File(name=name, file_name="uuid.csv", is_private=1, content=b"new payload",
                        attached_to_doctype="Prepared Report", attached_to_name="PR")
        self.frame = Row(doc=doc, file_doc=doc, sealed_artifact=None,
                         filename="uuid.csv", content=b"new payload")
        return doc

    def test_ordinary_permission_and_content_preserve_native_arguments(self):
        doc = self.add_file()
        self.assertTrue(doc.has_permission("write", debug=True, user="actor"))
        self.assertEqual(doc.get_content(encodings=("utf-8",)), "amount\n42\n")
        self.assertIn(("permission", "write", True, "actor"), NativeFile.calls)
        self.assertIn(("content", ("utf-8",)), NativeFile.calls)
        self.artifacts.check_artifact.assert_not_called()

    def test_ordinary_public_share_and_copy_remain_native(self):
        doc = self.add_file(private=0)
        self.assertTrue(doc.is_downloadable())
        self.assertTrue(doc.has_permission("share"))
        doc.flags.copy_from_existing_file = True
        self.assertEqual(doc.before_insert(), "copy")
        self.assertIsNone(doc.validate_share(Row()))

    def test_ordinary_encoded_copy_preserves_native_single_decode(self):
        original = self.add_file()
        doc = self.File(name=None, file_name="F.csv", file_url=quote(original.file_url, safe=""),
                        is_private=1, owner="actor")
        doc.flags.copy_from_existing_file = True
        self.assertEqual(doc.before_insert(), "copy")
        self.assertEqual(doc.file_url, original.file_url)

    def test_authorized_copy_alias_preserves_native_create_and_skips_write(self):
        original = self.add_file(artifact="A")
        doc = self.File(name=None, file_name="F.csv", file_url=original.file_url,
                        is_private=1, owner="actor")
        doc.flags.copy_from_existing_file = True
        self.assertTrue(doc.has_permission("create"))
        self.assertEqual(doc.before_insert(), "copy")
        self.assertFalse(any(call[0] == "write" for call in NativeFile.calls))

    def test_encoded_root_alias_is_checked_before_native_single_decode_and_write(self):
        original = self.add_file(artifact="A")
        self.denied_roots.add("A")
        doc = self.File(name=None, file_name="F.csv", file_url=quote(original.file_url, safe=""),
                        is_private=1, owner="actor")
        doc.flags.copy_from_existing_file = True
        with self.assertRaises(PermissionError):
            doc.before_insert()
        self.assertFalse(any(call[0] == "before_insert" for call in NativeFile.calls))

    def test_scoped_native_denial_stays_denied(self):
        doc = self.add_file(artifact="A")
        doc.native_allowed = False
        self.assertFalse(doc.is_downloadable())
        self.assertFalse(doc.has_permission())

    def test_owner_and_fid_alias_cannot_bypass_root(self):
        self.add_file("ROOT", artifact="A", parent=("Prepared Report", "PR"))
        alias = self.add_file("ALIAS", url="/private/files/ROOT.csv", parent=("ordinary", "other"))
        self.denied_roots.add("A")
        self.assertFalse(alias.is_downloadable())
        self.assertFalse(alias.has_permission())
        with self.assertRaisesRegex(PermissionError, "来源不可读") as error:
            alias.get_content()
        self.assertNotIn("secret-source-id", str(error.exception))

    def test_union_of_multiple_receipt_roots(self):
        doc = self.add_file("F", artifact="A")
        self.add_file("F2", url=doc.file_url, artifact="B")
        self.denied_roots.add("B")
        self.assertFalse(doc.is_downloadable())
        self.assertEqual({call.args[0] for call in self.artifacts.check_artifact.call_args_list}, {"A", "B"})

    def test_same_site_url_and_missing_hash_alias_are_in_inventory(self):
        doc = self.add_file(artifact="A")
        alias = self.add_file("ALIAS", url=doc.file_url)
        self.rows["ALIAS"].file_url = "https://local" + doc.file_url
        self.rows["ALIAS"].content_hash = None
        identity = self.subject.canonical_file_identity(alias)
        self.assertEqual({item["name"] for item in identity["aliases"]}, {"F", "ALIAS"})
        self.denied_roots.add("A")
        self.assertFalse(self.get_doc("File", "ALIAS").is_downloadable())

    def test_legacy_prepared_custom_and_report_exports_are_denied(self):
        for parent in (("Prepared Report", "PR"), ("Report", "CUSTOM")):
            doc = self.add_file(parent=parent)
            with self.assertRaisesRegex(PermissionError, "旧快照缺少来源校验"):
                doc.get_content()
            self.assertFalse(doc.is_downloadable())
        self.assertFalse(any(call[0] == "content" for call in NativeFile.calls))

    def test_ordinary_prepared_attachment_stays_native(self):
        self.prepared["PR-ORD"] = "General Ledger"
        doc = self.add_file(parent=("Prepared Report", "PR-ORD"))
        self.assertEqual(doc.get_content(), "amount\n42\n")

    def test_wrong_current_binding_cannot_hide_persisted_scope(self):
        doc = self.add_file(artifact="A")
        doc.attached_to_doctype = "ordinary"
        doc.attached_to_name = "wrong"
        self.assertFalse(doc.is_downloadable())

    def test_ordinary_in_memory_content_cannot_replace_sealed_bytes(self):
        doc = self.add_file(artifact="A")
        doc.content = b"fabricated"
        with self.assertRaises(PermissionError):
            doc.get_content()
        self.assertFalse(any(call[0] == "content" for call in NativeFile.calls))

    def test_byte_change_during_native_read_does_not_return(self):
        doc = self.add_file(artifact="A")
        NativeFile.read_effect = lambda selected: Path(selected.get_full_path()).write_bytes(b"changed")
        with self.assertRaisesRegex(PermissionError, "来源不可读"):
            doc.get_content()

    def test_alias_change_during_native_read_does_not_return(self):
        doc = self.add_file(artifact="A")
        NativeFile.read_effect = lambda selected: self.rows.update(
            {"ALIEN": Row(**{**self.rows["F"], "name": "ALIEN", "attached_to_name": "new"})})
        with self.assertRaises(PermissionError):
            doc.get_content()

    def test_source_change_during_native_read_does_not_return(self):
        doc = self.add_file(artifact="A")
        NativeFile.read_effect = lambda selected: self.source_versions.update(A="source-v2")
        with self.assertRaises(PermissionError):
            doc.get_content()

    def test_parent_lineage_signature_stable_across_authorized_consumers(self):
        doc = self.add_file(artifact="A")
        first = self.subject.check_parent_file(doc)
        self.frappe.session.user = "second-authorized-user"
        second = self.subject.check_parent_file(doc)
        self.assertEqual(first.signature, second.signature)
        self.assertNotEqual(first.user, second.user)

    def test_effective_user_change_during_one_read_still_denied(self):
        doc = self.add_file(artifact="A")
        NativeFile.read_effect = lambda selected: self.frappe.session.update(user="second-authorized-user")
        with self.assertRaises(PermissionError):
            doc.get_content()

    def test_native_encoding_and_ole_bytes_are_preserved(self):
        for data, expected in ((b"\xef\xbb\xbftext", "text"), (b"OLE!binary", b"OLE!binary")):
            doc = self.add_file(data=data, artifact="A")
            self.assertEqual(doc.get_content(), expected)

    def test_prepared_gzip_explicit_empty_encodings_stays_binary(self):
        data = gzip.compress(b'{"result":[[42]]}')
        doc = self.add_file(data=data, artifact="A")
        self.assertEqual(doc.get_content(encodings=()), data)
        self.assertIn(("content", ()), NativeFile.calls)

    def test_private_flag_or_path_change_denied_before_move(self):
        doc = self.add_file(artifact="A")
        doc.is_private = 0
        with self.assertRaises(PermissionError):
            doc.handle_is_private_changed()
        self.assertFalse(any(call[0] == "move" for call in NativeFile.calls))
        self.assertTrue(Path(NativeFile.get_full_path(self.get_doc("File", "F"))).exists())

    def test_public_namespace_symlink_to_private_file_denied(self):
        doc = self.add_file(artifact="A")
        link = self.site / "public" / "files" / "alias.csv"
        try:
            link.symlink_to(NativeFile.get_full_path(doc))
        except OSError:
            self.skipTest("Host does not allow isolated symlink creation")
        self.rows["ALIAS"] = Row(**{**self.rows["F"], "name": "ALIAS", "file_url": "/files/alias.csv"})
        self.assertFalse(doc.is_downloadable())

    def test_public_namespace_realpath_alias_is_denied_without_host_symlink_permission(self):
        doc = self.add_file(artifact="A")
        private = NativeFile.get_full_path(doc)
        public = str(self.site / "public" / "files" / "alias.csv")
        self.rows["ALIAS"] = Row(**{**self.rows["F"], "name": "ALIAS", "file_url": "/files/alias.csv"})
        realpath = os.path.realpath
        with patch.object(self.subject.os.path, "realpath",
                          side_effect=lambda path: realpath(private) if str(path) == public else realpath(path)):
            self.assertFalse(doc.is_downloadable())

    def test_unregistered_new_scoped_file_never_reaches_first_write(self):
        doc = self.pending()
        self.frame = None
        doc.flags.ignore_permissions = True
        doc.flags.ignore_validate = True
        with self.assertRaises(PermissionError):
            doc.before_insert()
        self.assertFalse(any(call[0] == "before_insert" for call in NativeFile.calls))
        self.assertFalse((self.site / "private" / "files" / "uuid.csv").exists())

    def test_generation_requires_exact_object_not_flags_or_equal_bytes(self):
        doc = self.pending()
        fake = self.File(**{**doc, "flags": Row(report_proof="client")})
        with self.assertRaises(PermissionError):
            fake.before_insert()
        self.assertFalse(any(call[0] == "before_insert" for call in NativeFile.calls))

    def test_registered_generation_public_or_bind_mutation_rejected_before_write(self):
        for field, value in (("is_private", 0), ("attached_to_name", "other"), ("content", b"other")):
            doc = self.pending()
            doc[field] = value
            with self.assertRaises(PermissionError):
                doc.before_insert()
        self.assertFalse(any(call[0] == "before_insert" for call in NativeFile.calls))
        self.assertFalse((self.site / "private" / "files" / "uuid.csv").exists())

    def test_registered_unnamed_private_file_skips_both_native_dedup_steps(self):
        doc = self.pending()
        self.add_file("OLD", data=doc.content)
        self.assertEqual(doc.before_insert(), "insert")
        self.assertEqual(Path(NativeFile.get_full_path(doc)).read_bytes(), b"new payload")
        saves = [call for call in NativeFile.calls if call[0] == "save"]
        self.assertEqual(saves[0][3:], (True, True))
        self.assertFalse(any(call[0] == "duplicate" for call in NativeFile.calls))
        self.assertNotEqual(doc.file_url, self.rows["OLD"].file_url)

    def test_generation_cannot_overwrite_existing_uuid_path(self):
        doc = self.pending()
        path = self.site / "private" / "files" / "uuid.csv"
        path.write_bytes(b"ordinary original")
        with self.assertRaises(PermissionError):
            doc.before_insert()
        self.assertEqual(path.read_bytes(), b"ordinary original")

    def test_exclusive_rollback_removes_only_new_path_despite_equal_ordinary_hash(self):
        doc = self.pending()
        ordinary = self.add_file("OLD", data=doc.content)
        doc.before_insert()
        self.frame = None
        for callback in self.rollback:
            callback()
        self.assertFalse((self.site / "private" / "files" / "uuid.csv").exists())
        self.assertEqual(Path(NativeFile.get_full_path(ordinary)).read_bytes(), b"new payload")

    def test_exclusive_rollback_does_not_delete_or_restore_into_replaced_inode(self):
        doc = self.pending()
        doc.before_insert()
        path = self.site / "private" / "files" / "uuid.csv"
        replacement = self.site / "private" / "files" / "ordinary-new.csv"
        replacement.write_bytes(b"ordinary replacement")
        os.replace(replacement, path)
        self.frame = None
        for callback in self.rollback:
            callback()
        self.assertEqual(path.read_bytes(), b"ordinary replacement")
        self.assertFalse(doc.flags.new_file)
        self.assertFalse(doc.flags.original_content)

    def test_hash_reuse_candidate_checked_before_native_save(self):
        self.add_file("ROOT", artifact="A")
        self.denied_roots.add("A")
        doc = self.File(file_name="ordinary.csv", content=b"amount\n42\n", name=None)
        with self.assertRaises(PermissionError):
            doc.save_file(content=doc.content)
        self.assertFalse(any(call[0] == "save" for call in NativeFile.calls))

    def test_unselected_equal_hash_in_different_path_does_not_restrict_ordinary_upload(self):
        ordinary = self.add_file("ORD")
        self.add_file("ROOT", artifact="A")
        self.denied_roots.add("A")
        doc = self.File(file_name="ordinary.csv", content=b"amount\n42\n", name=None)
        self.assertEqual(doc.save_file(content=doc.content), "duplicate")
        self.assertEqual(doc.file_url, ordinary.file_url)
        self.artifacts.check_artifact.assert_not_called()

    def test_hash_assignment_candidate_respects_native_attachment_filter(self):
        self.add_file("ROOT", artifact="A")
        ordinary = self.add_file("ORD", parent=("ordinary", "same"))
        self.denied_roots.add("A")
        doc = self.File(file_name="ORD.csv", file_url=ordinary.file_url, name=None, owner="actor",
                        content_hash=ordinary.content_hash, attached_to_doctype="ordinary",
                        attached_to_name="same")
        self.assertEqual(doc.validate_duplicate_entry(), "checked")
        self.artifacts.check_artifact.assert_not_called()

    def test_scoped_write_and_share_cannot_use_native_owner_fast_grant(self):
        doc = self.add_file(artifact="A")
        with self.assertRaises(PermissionError):
            doc.save_file(content=b"replacement")
        self.assertFalse(doc.has_permission("share"))
        with self.assertRaises(PermissionError):
            doc.validate_share(Row())
        with self.assertRaises(PermissionError):
            self.subject.prevent_report_file_docshare(Row(share_doctype="File", share_name="F",
                                                          flags=Row(ignore_validate=True)))

    def test_existing_alias_docshare_denies_content_and_download_even_if_sources_readable(self):
        doc = self.add_file(artifact="A")
        self.shares.append(Row(name="OLD-INDEPENDENT-SHARE"))
        self.assertFalse(doc.is_downloadable())
        self.assertFalse(doc.has_permission())
        with self.assertRaises(PermissionError):
            doc.get_content()

    def test_query_conditions_keep_ordinary_rows_and_deny_only_unreadable_scope(self):
        self.add_file("ORD")
        self.add_file("ROOT", artifact="A")
        self.denied_roots.add("A")
        condition = self.subject.report_file_query_conditions()
        self.assertIn("'ROOT'", condition)
        self.assertNotIn("'ORD'", condition)

    def test_existing_scoped_share_cannot_revive_pqc_denial(self):
        self.add_file(artifact="A")
        self.shares.append(Row(name="BAD-SHARE"))
        with self.assertRaisesRegex(PermissionError, "不能单独分享"):
            self.subject.report_file_query_conditions()

    def test_no_store_applies_only_to_successful_scoped_download_and_clears_frame(self):
        doc = self.add_file(artifact="A")
        self.frappe.local.request = Row(path="/private/files/F.csv")
        self.assertTrue(doc.is_downloadable())
        response = Response()
        response.headers["X-Accel-Redirect"] = "/protected/private/files/F.csv"
        self.subject.after_report_file_request(response)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store, max-age=0")
        ordinary = Response()
        self.subject.after_report_file_request(ordinary)
        self.assertEqual(ordinary.headers["Cache-Control"], "native-cache")

    def test_change_after_x_accel_authorization_removes_redirect(self):
        doc = self.add_file(artifact="A")
        self.frappe.local.request = Row(path="/private/files/F.csv")
        self.assertTrue(doc.is_downloadable())
        self.denied_roots.add("A")
        response = Response()
        response.headers["X-Accel-Redirect"] = "/protected/private/files/F.csv"
        self.subject.after_report_file_request(response)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("X-Accel-Redirect", response.headers)
        self.assertNotIn(b"secret-source-id", response.data)


if __name__ == "__main__":
    unittest.main()
