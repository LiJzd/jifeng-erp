"""Prepared models: no database, queue, cache, filesystem, or native execution."""

from contextlib import contextmanager
from contextvars import ContextVar
import copy
import gzip
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]


class Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value

    def as_dict(self):
        return dict(self)

    def is_new(self):
        return bool(self.get("__new"))


def freeze(value):
    if isinstance(value, dict):
        return tuple((key, freeze(item)) for key, item in sorted(value.items())
                     if not key.startswith("__"))
    if isinstance(value, (tuple, list)):
        return tuple(freeze(item) for item in value)
    return type(value).__name__, repr(value)


class PreparedModels(unittest.TestCase):
    def setUp(self):
        self.docs, self.events, self.roots, self.proofs = {}, [], {}, {}
        self.source_allowed, self.native_allowed = True, True
        self.shared, self.on_bytes, self.on_proof, self.on_commit = set(), None, None, None
        self.generated = {"columns": [{"fieldname": "qty", "label": "Quantity"}],
                          "result": [[3]], "chart": {"datasets": [3]}, "total_row": 3}
        self.active = ContextVar("test_generation", default=None)
        test = self

        class Doc(Row):
            def save(doc, **kwargs):
                if doc.doctype == "Prepared Report" and (doc.doctype, doc.name) in test.docs:
                    if not test.subject.prepared_report_has_permission(doc, "write", "actor"):
                        raise PermissionError("native save final permission denied")
                    test.subject.before_validate_prepared_report(doc)
                test.events.append(("save", doc.doctype, doc.get("status")))
                test.docs[doc.doctype, doc.name] = copy.deepcopy(doc)
                if doc.doctype == "File":
                    test.file_written = True
                return doc

            def insert(doc, **kwargs):
                if doc.doctype == "Prepared Report":
                    doc.name = kwargs.get("set_name") or "NEW-PR"
                    if (doc.doctype, doc.name) in test.docs:
                        raise test.fr.DuplicateEntryError("duplicate")
                    doc.owner, doc.creation, doc.status = test.fr.session.user, "new-time", "Queued"
                else:
                    doc.name = "NOTIFICATION"
                return doc.save(**kwargs)

            def get_content(doc, encodings=None):
                test.assertEqual(encodings, ())
                test.events.append(("bytes", doc.name))
                if test.on_bytes:
                    test.on_bytes()
                return doc.content

        self.Doc = Doc
        fr = types.ModuleType("frappe")
        fr.PermissionError = PermissionError
        fr.DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})
        fr.session = Row(user="actor")
        fr.whitelist = lambda: lambda fn: fn
        fr._ = lambda value: "translated:" + value
        def throw(message, error=ValueError):
            raise error(message)
        fr.throw = throw
        def get_doc(dt, name=None, *, for_update=False):
            if isinstance(dt, dict):
                return Doc(dt)
            self.events.append(("get", dt, name, for_update))
            return copy.deepcopy(self.docs[dt, name])
        fr.get_doc = get_doc
        fr.get_value = lambda *args: None
        def permission(dt, ptype="read", *, doc=None, user=None, throw=False):
            self.events.append(("native_permission", doc.name, ptype, user))
            self.assertEqual(dt, "Prepared Report")
            return self.native_allowed and self.subject.prepared_report_has_permission(doc, ptype, user)
        fr.has_permission = Mock(side_effect=permission)
        fr.as_json = lambda value, **kwargs: json.dumps(value, sort_keys=True, **kwargs)
        fr.safe_encode = lambda value: value.encode()
        fr.scrub = lambda value: value.lower().replace(" ", "_")
        fr.utils = Row(now=lambda: "new-time", data=Row(format_datetime=lambda *args: "date"))
        fr.publish_realtime = Mock(side_effect=lambda *args, **kwargs: self.events.append(("publish",)))
        def exists(dt, filters):
            if dt == "DocShare":
                return filters["share_name"] in self.shared
            return (dt, filters) in self.docs
        def rollback():
            self.events.append(("rollback",))
            if hasattr(self, "committed"):
                self.docs = copy.deepcopy(self.committed)
            if getattr(self, "file_written", False):
                self.events.append(("native_file_cleanup",))
                self.file_written = False
            self.roots, self.proofs = {}, {}
        fr.db = Row(exists=Mock(side_effect=exists), escape=lambda value: repr(value),
                    rollback=Mock(side_effect=rollback))
        def all_rows(dt, **kwargs):
            if dt == "DocShare":
                return [Row(share_name=name) for name in self.shared]
            return [Row(name=name) for (kind, name) in self.docs if kind == dt]
        fr.get_all = Mock(side_effect=all_rows)
        fr.log_error, fr.get_traceback = Mock(), Mock()
        self.fr = fr

        artifacts = types.ModuleType("jifeng_erp.sample_source_report_artifacts")
        def resolve(name):
            self.events.append(("resolve", name))
            if name not in ("Open Work Orders", "Custom Work Orders"):
                return None
            return Row(requested_name=name, canonical_name="Open Work Orders",
                       report=Doc(doctype="Report", name="Open Work Orders", custom_columns=[]))
        artifacts.resolve_report = Mock(side_effect=resolve)
        def capture(name, filters, **kwargs):
            self.events.append(("capture", name, filters))
            if not self.source_allowed:
                raise PermissionError("PRIVATE SOURCE IDENTIFIER/AMOUNT")
            return Row() if resolve(name) is not None else None
        artifacts.capture = Mock(side_effect=capture)
        artifacts.artifacts_for_prepared = Mock(side_effect=lambda name: list(self.roots.get(name, [])))
        def proof(name, *, file_doc=None, content=None):
            self.events.append(("proof", name, content is not None))
            if not self.source_allowed:
                raise PermissionError("PRIVATE SOURCE IDENTIFIER/AMOUNT")
            saved = copy.deepcopy(self.proofs[name])
            if self.on_proof:
                self.on_proof()
            if content is not None:
                if content != saved.file_doc.content:
                    raise PermissionError("private wrong bytes")
                saved.content_verified = True
                saved.payload = gzip.decompress(content)
            return saved
        artifacts.check_artifact = Mock(side_effect=proof)
        @contextmanager
        def generation(name, filters, **kwargs):
            self.assertIn("commit", [event[0] for event in self.events])
            if not self.source_allowed:
                raise PermissionError("PRIVATE SOURCE")
            frame = Row(requested_name=name, filters=filters, output_options=kwargs["output_options"],
                        generation_id="UUID", file_doc=None, prepared_report=kwargs["prepared_report"],
                        resolved=resolve(name), artifact_kind="JSON", user="actor", sealed_artifact=None)
            token = self.active.set(frame)
            self.events.append(("frame_enter",))
            try:
                yield frame
            finally:
                self.active.reset(token)
                self.events.append(("frame_exit",))
        artifacts.generation = generation
        artifacts.current_frame = lambda: self.active.get()
        def pending(frame, *, payload, content, file_name):
            self.assertIs(frame, self.active.get())
            file_doc = Doc(doctype="File", name="FILE-UUID", file_name="UUID.json.gz",
                           attached_to_doctype="Prepared Report", attached_to_name=frame.prepared_report,
                           is_private=1, content=content)
            frame.file_doc = file_doc
            self.events.append(("new_file", payload, content, file_name))
            return file_doc
        artifacts.new_pending_file = Mock(side_effect=pending)
        def seal(frame, file_doc):
            self.assertIs(frame.file_doc, file_doc)
            if not self.source_allowed:
                raise PermissionError("PRIVATE CHANGED SOURCE")
            self.events.append(("seal",))
            self.bind_json(frame.prepared_report, file_doc=file_doc)
            frame.sealed_artifact = self.proofs["ART"].artifact
        artifacts.seal = Mock(side_effect=seal)
        self.artifacts = artifacts
        native = types.ModuleType("frappe.core.doctype.prepared_report.prepared_report")
        native.REPORT_TIMEOUT = 1500
        def update(name):
            self.assertIsNone(self.active.get())
            self.docs["Prepared Report", name].status = "Started"
            self.events.append(("commit",))
            self.committed = copy.deepcopy(self.docs)
            if self.on_commit:
                self.on_commit()
        native.update_job_id = Mock(side_effect=update)
        native.generate_report = Mock(return_value="ordinary worker")
        native.get_completed_prepared_report = Mock(return_value="PR")
        native.process_filters_for_prepared_report = lambda value: (
            json.dumps(json.loads(value) if isinstance(value, str) else value, sort_keys=True,
                       separators=(",", ":")))
        native.get_reports_in_queued_state = Mock(return_value=[Row(name="PR")])
        self.native = native
        queue = types.ModuleType("frappe.utils.background_jobs")
        queue.enqueue = Mock()
        self.queue = queue
        output = types.ModuleType("jifeng_erp.sample_source_report_output")
        def finalizer(report, filters=None, user=None, *, context=None):
            self.assertIsNotNone(self.active.get())
            self.assertEqual(user, "actor")
            self.assertEqual(context.requested_name, "Open Work Orders")
            self.events.append(("finalizer",))
            return copy.deepcopy(self.generated)
        output.generate_report_result = Mock(side_effect=finalizer)
        self.output = output
        monitor = types.ModuleType("frappe.monitor")
        monitor.add_data_to_monitor = Mock()
        query = types.ModuleType("frappe.desk.query_report")
        query.get_prepared_report_result = Mock(return_value={"ordinary": True})
        self.query = query
        samples = types.ModuleType("jifeng_erp.sample_orders")
        samples._freeze = freeze
        app = types.ModuleType("jifeng_erp")
        app.sample_source_report_artifacts = artifacts
        ppkg = types.ModuleType("frappe.core.doctype.prepared_report")
        ppkg.prepared_report = native
        resource = types.ModuleType("resource")
        resource.RUSAGE_SELF = 0
        resource.getrusage = lambda kind: Row(ru_maxrss=1024)
        self.modules = {"frappe": fr, "jifeng_erp": app,
                        "jifeng_erp.sample_orders": samples,
                        "jifeng_erp.sample_source_report_artifacts": artifacts,
                        "jifeng_erp.sample_source_report_output": output,
                        "frappe.core.doctype.prepared_report": ppkg,
                        "frappe.core.doctype.prepared_report.prepared_report": native,
                        "frappe.utils.background_jobs": queue, "frappe.monitor": monitor,
                        "frappe.desk.query_report": query, "resource": resource}
        self.patch = patch.dict(sys.modules, self.modules)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        spec = importlib.util.spec_from_file_location("prepared_under_test",
                      ROOT / "jifeng_erp/sample_source_prepared_reports.py")
        self.subject = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.subject)
        self.pr = self.doc("PR")

    def doc(self, name="PR", **values):
        doc = self.Doc(doctype="Prepared Report", name=name, report_name="Open Work Orders",
                       filters='{"company":"JF"}', owner="actor", status="Completed",
                       creation="old-time", error_message=None, **values)
        self.docs["Prepared Report", name] = doc
        return doc

    def bind_json(self, name="PR", *, artifact="ART", file_doc=None):
        payload = json.dumps(self.generated).encode()
        file_doc = file_doc or self.Doc(doctype="File", name="FILE", file_name="file.json.gz",
                                       content=gzip.compress(payload))
        self.proofs[artifact] = Row(artifact=Row(name=artifact, artifact_kind="JSON"),
                                   file_doc=file_doc, prepared_report=name,
                                   report_name=self.docs["Prepared Report", name].report_name,
                                   file_name=file_doc.file_name, payload=None, content_verified=False)
        self.roots.setdefault(name, []).append(artifact)
        return file_doc

    def mixed(self, name="PR"):
        test = self
        class Native(self.Doc):
            def has_permission(self, permtype="read", *, debug=False, user=None):
                return test.native_allowed
            def get_prepared_data(self, with_file_name=False):
                return (b"ordinary", "ordinary.gz") if with_file_name else b"ordinary"
            def after_insert(self):
                return "ordinary insert"
        class Mixed(self.subject.SampleSourcePreparedReportMixin, Native):
            pass
        return Mixed(self.docs["Prepared Report", name])

    def test_ordinary_read_has_permission_insert_and_worker_keep_native(self):
        self.pr.report_name = "Other Report"
        doc = self.mixed()
        self.assertEqual(doc.get_prepared_data(), b"ordinary")
        self.assertTrue(doc.has_permission())
        self.assertEqual(doc.after_insert(), "ordinary insert")
        self.assertEqual(self.subject.generate_report("PR"), "ordinary worker")
        self.artifacts.capture.assert_not_called()
        self.artifacts.check_artifact.assert_not_called()

    def test_ordinary_worker_exception_retains_native_semantics(self):
        self.pr.report_name = "Other Report"
        self.native.generate_report.side_effect = RuntimeError("ordinary")
        with self.assertRaisesRegex(RuntimeError, "ordinary"):
            self.subject.generate_report("PR")

    def test_completed_legacy_does_not_read_or_silently_rebuild(self):
        with self.assertRaisesRegex(PermissionError, self.subject.LEGACY_MESSAGE):
            self.mixed().get_prepared_data()
        self.output.generate_report_result.assert_not_called()
        self.assertFalse(self.subject.prepared_report_has_permission(self.pr))

    def test_bound_json_payload_and_native_filename_shape(self):
        self.bind_json()
        data, name = self.mixed().get_prepared_data(True)
        self.assertEqual(json.loads(data), self.generated)
        self.assertEqual(name, "file.json.gz")
        self.assertEqual(self.artifacts.check_artifact.call_count, 3)

    def test_unsaved_narrow_filters_and_other_report_do_not_rewrite_stored_binding(self):
        self.bind_json()
        doc = self.mixed()
        doc.filters, doc.report_name = "{}", "Other Report"
        self.assertEqual(json.loads(doc.get_prepared_data()), self.generated)
        self.assertEqual(self.proofs["ART"].report_name, "Open Work Orders")

    def test_persisted_report_rename_cannot_make_bound_artifact_ordinary(self):
        self.bind_json()
        self.pr.report_name = "Other Report"
        with self.assertRaises(PermissionError):
            self.mixed().get_prepared_data()

    def test_hidden_source_after_file_read_refuses_final_bytes(self):
        self.bind_json()
        self.on_bytes = lambda: setattr(self, "source_allowed", False)
        with self.assertRaises(PermissionError):
            self.mixed().get_prepared_data()

    def test_pr_change_during_read_is_denied(self):
        self.bind_json()
        self.on_bytes = lambda: setattr(self.pr, "filters", "{}")
        with self.assertRaises(PermissionError):
            self.mixed().get_prepared_data()

    def test_permission_revoked_during_read_is_denied(self):
        self.bind_json()
        self.on_bytes = lambda: setattr(self, "native_allowed", False)
        with self.assertRaises(PermissionError):
            self.mixed().get_prepared_data()

    def test_wrong_bytes_unverified_proof_and_ambiguous_json_are_denied(self):
        file_doc = self.bind_json()
        file_doc.content = b"wrong"
        with self.assertRaises((PermissionError, gzip.BadGzipFile)):
            self.mixed().get_prepared_data()
        self.bind_json(artifact="ART2")
        with self.assertRaises(PermissionError):
            self.mixed().get_prepared_data()

    def test_a_precheck_without_completed_byte_authorization_cannot_return_payload(self):
        self.bind_json()
        original = self.artifacts.check_artifact.side_effect
        def not_verified(*args, **kwargs):
            result = original(*args, **kwargs)
            result.content_verified = False
            return result
        self.artifacts.check_artifact.side_effect = not_verified
        with self.assertRaises(PermissionError):
            self.mixed().get_prepared_data()

    def test_native_share_or_ignore_permission_cannot_restore_source_denial(self):
        self.bind_json()
        self.source_allowed = False
        self.assertFalse(self.mixed().has_permission())
        self.shared.add("PR")
        self.assertFalse(self.mixed().has_permission())

    def test_pending_metadata_source_gate_and_legacy_error_locals(self):
        self.pr.status = "Queued"
        self.assertTrue(self.subject.prepared_report_has_permission(self.pr))
        self.source_allowed = False
        self.assertFalse(self.subject.prepared_report_has_permission(self.pr))
        self.source_allowed = True
        self.pr.error_message = "PRIVATE OLD CONTEXT"
        self.assertFalse(self.subject.prepared_report_has_permission(self.pr))
        self.pr.status, self.pr.error_message = "Error", self.subject.GENERATION_ERROR
        self.assertTrue(self.subject.prepared_report_has_permission(self.pr))

    def test_wrong_user_denied_scoped_native_context_restored(self):
        self.bind_json()
        self.assertFalse(self.subject.prepared_report_has_permission(self.pr, user="other"))
        self.assertFalse(self.mixed().has_permission(user="other"))
        self.assertEqual(self.subject._NATIVE_GUARDS.get(), frozenset())

    def test_query_names_hidden_source_and_native_roles(self):
        self.bind_json()
        self.assertIn("'PR'", self.subject.prepared_report_query_conditions())
        self.source_allowed = False
        self.assertEqual(self.subject.prepared_report_query_conditions(), "1=0")
        self.source_allowed, self.native_allowed = True, False
        self.assertEqual(self.subject.prepared_report_query_conditions(), "1=0")

    def test_hidden_report_role_does_not_expose_name_or_block_ordinary_list(self):
        self.doc("ORDINARY").report_name = "Other Report"
        original = self.artifacts.resolve_report.side_effect
        def hidden(name):
            if name == "Open Work Orders":
                raise PermissionError("PRIVATE REPORT")
            return original(name)
        self.artifacts.resolve_report.side_effect = hidden
        condition = self.subject.prepared_report_query_conditions()
        self.assertNotIn("'PR'", condition)
        self.assertIn("'ORDINARY'", condition)
        self.assertFalse(self.mixed().has_permission())

    def test_ordinary_select_only_name_remains_link_selectable_not_read_granted(self):
        self.pr.report_name = "Other Report"
        self.fr.has_permission.side_effect = lambda dt, ptype="read", **kwargs: ptype == "select"
        self.assertIn("'PR'", self.subject.prepared_report_query_conditions())
        self.native_allowed = False
        self.assertFalse(self.mixed().has_permission())

    def test_share_event_two_three_argument_native_ordinary_and_scoped(self):
        self.pr.report_name = "Other Report"
        self.subject.validate_prepared_report_share(self.pr, "validate_share", Row())
        self.subject.before_validate_docshare(Row(share_doctype="Prepared Report", share_name="PR"))
        self.pr.report_name = "Open Work Orders"
        with self.assertRaises(PermissionError):
            self.subject.validate_prepared_report_share(self.pr, Row())
        with self.assertRaises(PermissionError):
            self.subject.before_validate_docshare(Row(share_doctype="Prepared Report", share_name="PR"))

    def test_orphan_and_named_new_binding_share_denied(self):
        with self.assertRaises(PermissionError):
            self.subject.before_validate_docshare(Row(share_doctype="Prepared Report", share_name="MISSING"))
        self.shared.add("PR")
        self.pr["__new"] = True
        with self.assertRaises(PermissionError):
            self.subject.before_validate_prepared_report(self.pr)
        with self.assertRaises(PermissionError):
            self.subject.assert_prepared_share_invariant()

    def test_scoped_legacy_binding_cannot_be_renamed_ordinary_or_reowned(self):
        for field, value in (("report_name", "Other Report"), ("owner", "other"),
                             ("filters", "{}")):
            candidate = copy.deepcopy(self.pr)
            candidate[field] = value
            with self.assertRaises(PermissionError):
                self.subject.before_validate_prepared_report(candidate)
        candidate = copy.deepcopy(self.pr)
        candidate.status, candidate.job_id = "Started", "native-job"
        candidate.filters = '{ "company": "JF" }'
        self.subject.before_validate_prepared_report(candidate)

    def test_ordinary_stored_binding_native_edit_is_unchanged(self):
        self.pr.report_name = "Other Report"
        candidate = copy.deepcopy(self.pr)
        candidate.filters, candidate.owner = "{}", "other"
        self.subject.before_validate_prepared_report(candidate)

    def test_after_insert_only_fixed_commit_delayed_owner_bound_worker(self):
        self.pr.status = "Queued"
        self.mixed().after_insert()
        args, kwargs = self.queue.enqueue.call_args
        self.assertIs(args[0], self.subject.generate_report)
        self.assertEqual(kwargs, dict(queue="long", prepared_report="PR", timeout=1500,
                                     enqueue_after_commit=True, at_front_when_starved=True))
        self.pr.owner = "other"
        with self.assertRaises(PermissionError):
            self.mixed().after_insert()

    def test_worker_success_full_finalizer_native_json_sealed_completed_notification(self):
        self.pr.status = "Queued"
        self.subject.generate_report("PR")
        self.assertEqual(self.docs["Prepared Report", "PR"].status, "Completed")
        event_names = [event[0] for event in self.events]
        self.assertLess(event_names.index("commit"), event_names.index("frame_enter"))
        self.assertLess(event_names.index("finalizer"), event_names.index("seal"))
        self.assertLess(event_names.index("seal"), event_names.index("frame_exit"))
        self.assertIn(("save", "Notification Log", None), self.events)
        new_file = next(event for event in self.events if event[0] == "new_file")
        self.assertEqual(json.loads(new_file[1]), self.generated)
        self.assertEqual(gzip.decompress(new_file[2]), new_file[1])
        self.assertEqual(new_file[3], "open_work_orders_date.json.gz")
        self.assertIsNone(self.active.get())
        self.fr.log_error.assert_not_called()
        self.fr.get_traceback.assert_not_called()

    def test_worker_current_session_wrong_owner_or_started_never_overwrites(self):
        self.pr.status, self.pr.owner = "Queued", "other"
        self.subject.generate_report("PR")
        self.native.update_job_id.assert_not_called()
        self.assertEqual(self.pr.status, "Queued")
        self.pr.status, self.pr.owner = "Started", "actor"
        self.subject.generate_report("PR")
        self.native.update_job_id.assert_not_called()

    def test_worker_rechecks_after_native_early_commit(self):
        self.pr.status = "Queued"
        self.on_commit = lambda: setattr(self, "source_allowed", False)
        self.subject.generate_report("PR")
        self.assertEqual(self.docs["Prepared Report", "PR"].status, "Error")
        self.assertEqual(self.docs["Prepared Report", "PR"].error_message, self.subject.GENERATION_ERROR)
        self.output.generate_report_result.assert_not_called()
        self.assertIsNone(self.active.get())
        self.fr.log_error.assert_not_called()

    def test_worker_file_final_source_failure_rolls_back_native_file_callback(self):
        self.pr.status = "Queued"
        self.artifacts.seal.side_effect = PermissionError("PRIVATE BODY LOCALS")
        self.subject.generate_report("PR")
        self.assertEqual(self.docs["Prepared Report", "PR"].status, "Error")
        self.assertIn(("rollback",), self.events)
        self.assertIn(("native_file_cleanup",), self.events)
        self.assertEqual(self.roots, {})
        self.assertIsNone(self.active.get())
        self.fr.log_error.assert_not_called()
        self.fr.get_traceback.assert_not_called()

    def test_worker_cleanup_failure_not_forwarded_to_native_context_logger(self):
        self.pr.status = "Queued"
        self.output.generate_report_result.side_effect = PermissionError("PRIVATE CONTEXT")
        self.fr.db.rollback.side_effect = RuntimeError("cleanup unknown")
        with self.assertRaisesRegex(PermissionError, self.subject.GENERATION_ERROR) as caught:
            self.subject.generate_report("PR")
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertIsNone(self.active.get())
        self.fr.log_error.assert_not_called()
        self.fr.get_traceback.assert_not_called()

    def test_worker_unknown_rollback_reaches_native_except_not_success_commit(self):
        self.pr.status = "Queued"
        self.output.generate_report_result.side_effect = PermissionError("PRIVATE ROWS/FILTERS/AMOUNTS")
        original = self.fr.db.rollback.side_effect
        attempts = []
        def rollback():
            attempts.append("rollback")
            if len(attempts) == 1:
                raise RuntimeError("unknown rollback")
            original()
        self.fr.db.rollback.side_effect = rollback
        committed = Mock()
        frames = []
        try:
            self.subject.generate_report("PR")
        except Exception as error:
            trace = error.__traceback__
            while trace:
                frames.append(trace.tb_frame.f_code.co_name)
                if trace.tb_frame.f_code.co_name == "generate_report":
                    self.assertEqual(set(trace.tb_frame.f_locals), {"prepared_report", "result"})
                trace = trace.tb_next
            self.assertEqual(str(error), self.subject.GENERATION_ERROR)
            self.fr.db.rollback()
        else:
            committed()
        committed.assert_not_called()
        self.assertNotIn("_generate_report_body", frames)
        self.assertEqual(attempts, ["rollback", "rollback"])

    def test_completed_status_save_requires_exact_sealed_active_json_frame(self):
        self.pr.status = "Started"
        candidate = copy.deepcopy(self.pr)
        candidate.status, candidate.report_end_time = "Completed", "new-time"
        frame = Row(artifact_kind="JSON", prepared_report="PR", user="actor",
                    sealed_artifact=None)
        token = self.active.set(frame)
        try:
            with self.assertRaises(PermissionError):
                self.subject._save_status(candidate, "Completed", frame=frame)
            self.bind_json()
            frame.sealed_artifact = self.proofs["ART"].artifact
            self.subject._save_status(candidate, "Completed", frame=frame)
        finally:
            self.active.reset(token)
        self.assertIsNone(self.subject._STATUS_SAVE.get())
        self.assertEqual(self.docs["Prepared Report", "PR"].status, "Completed")

    def test_status_context_wrong_object_read_user_or_extra_business_change_denied(self):
        self.pr.status = "Started"
        candidate = copy.deepcopy(self.pr)
        candidate.status, candidate.error_message = "Error", self.subject.GENERATION_ERROR
        context = {"doc": candidate, "kind": "Error", "frame": None, "user": "actor"}
        token = self.subject._STATUS_SAVE.set(context)
        self.source_allowed = False
        try:
            self.assertTrue(self.subject.prepared_report_has_permission(candidate, "write", "actor"))
            self.assertFalse(self.subject.prepared_report_has_permission(candidate, "read", "actor"))
            self.assertFalse(self.subject.prepared_report_has_permission(candidate, "write", "other"))
            self.assertFalse(self.subject.prepared_report_has_permission(copy.deepcopy(candidate), "write", "actor"))
            candidate.job_id = "forged-new-job"
            self.assertFalse(self.subject.prepared_report_has_permission(candidate, "write", "actor"))
        finally:
            self.subject._STATUS_SAVE.reset(token)
        self.assertIsNone(self.subject._STATUS_SAVE.get())

    def test_completed_native_save_rechecks_source_after_seal_and_records_only_generic_error(self):
        self.pr.status = "Queued"
        original = self.artifacts.seal.side_effect
        def revoke(*args):
            original(*args)
            self.source_allowed = False
        self.artifacts.seal.side_effect = revoke
        self.subject.generate_report("PR")
        saved = self.docs["Prepared Report", "PR"]
        self.assertEqual(saved.status, "Error")
        self.assertEqual(saved.error_message, self.subject.GENERATION_ERROR)
        self.assertEqual(self.roots, {})
        self.assertIn(("native_file_cleanup",), self.events)
        self.assertIsNone(self.subject._STATUS_SAVE.get())
        self.assertIsNone(self.active.get())

    def test_legacy_regenerate_new_identity_time_original_unchanged(self):
        before = copy.deepcopy(self.pr)
        result = self.subject.regenerate_prepared_report("PR")
        self.assertEqual(result, {"name": "NEW-PR"})
        self.assertEqual(self.docs["Prepared Report", "PR"], before)
        new = self.docs["Prepared Report", "NEW-PR"]
        self.assertEqual(new.filters, before.filters)
        self.assertEqual(new.creation, "new-time")
        self.assertEqual(new.owner, "actor")
        self.assertEqual(new.status, "Queued")

    def test_legacy_regenerate_current_sources_hidden_and_native_denied(self):
        self.source_allowed = False
        with self.assertRaises(PermissionError):
            self.subject.regenerate_prepared_report("PR")
        self.source_allowed, self.native_allowed = True, False
        self.pr.owner = "other"
        with self.assertRaises(PermissionError):
            self.subject.regenerate_prepared_report("PR")
        self.assertNotIn(("Prepared Report", "NEW-PR"), self.docs)

    def test_legacy_regenerate_source_check_cannot_edit_filters_between_authorization_and_insert(self):
        original = self.artifacts.capture.side_effect
        def change(*args, **kwargs):
            result = original(*args, **kwargs)
            self.pr.filters = "{}"
            return result
        self.artifacts.capture.side_effect = change
        with self.assertRaises(PermissionError):
            self.subject.regenerate_prepared_report("PR")
        self.assertNotIn(("Prepared Report", "NEW-PR"), self.docs)

    def test_dto_narrow_filters_never_replace_persisted_snapshot_no_error_log(self):
        self.bind_json()
        report = Row(name="Open Work Orders", report_name="Open Work Orders")
        result = self.subject.get_prepared_report_result(report, {}, "PR", "actor")
        self.assertTrue(result["prepared_report"])
        self.assertEqual(result["chart"], self.generated["chart"])
        self.assertEqual(result["total_row"], 3)
        self.assertEqual(result["columns"][0]["label"], "translated:Quantity")
        self.source_allowed = False
        with self.assertRaises(PermissionError):
            self.subject.get_prepared_report_result(report, {}, "PR", "actor")
        self.fr.log_error.assert_not_called()

    def test_dto_wrong_requested_report_and_ordinary_passthrough(self):
        report = Row(name="Custom Work Orders", report_name="Custom Work Orders")
        self.bind_json()
        result = self.subject.get_prepared_report_result(report, {}, "PR")
        self.assertEqual(result["doc"].report_name, "Open Work Orders")
        report.name, report.report_name = "Other Report", "Other Report"
        self.assertEqual(self.subject.get_prepared_report_result(report, {}, "PR"), {"ordinary": True})

    def test_dto_same_canonical_owner_shortcut_still_checks_actual_saved_chain_sources(self):
        self.bind_json()
        self.native_allowed = False
        report = Row(name="Custom Work Orders", report_name="Custom Work Orders")
        result = self.subject.get_prepared_report_result(report, {}, "PR")
        self.assertEqual(result["doc"].report_name, "Open Work Orders")
        self.source_allowed = False
        with self.assertRaises(PermissionError):
            self.subject.get_prepared_report_result(report, {}, "PR")
        self.assertEqual(self.proofs["ART"].report_name, "Open Work Orders")

    def test_queued_wrapper_get_all_cannot_bypass_source_gate(self):
        self.pr.status = "Queued"
        self.assertEqual(self.subject.get_reports_in_queued_state("Open Work Orders", {}), [Row(name="PR")])
        self.source_allowed = False
        with self.assertRaises(PermissionError):
            self.subject.get_reports_in_queued_state("Open Work Orders", {})

    def test_regeneration_target_explicit_and_native_implicit_no_old_bytes_or_filters_returned(self):
        for name in ("PR", None):
            result = self.subject.get_regeneration_target("Open Work Orders", name, {})
            self.assertEqual(result, {"eligible": True, "name": "PR"})
        self.native.get_completed_prepared_report.assert_called_with({}, "actor", "Open Work Orders")
        self.artifacts.check_artifact.assert_not_called()
        self.assertFalse(any(event[0] == "bytes" for event in self.events))
        self.assertEqual(self.artifacts.capture.call_args.args, ("Open Work Orders", self.pr.filters))

    def test_regeneration_target_current_role_source_share_or_changed_binding_denied(self):
        self.source_allowed = False
        self.assertEqual(self.subject.get_regeneration_target("Open Work Orders", "PR"), {"eligible": False})
        self.source_allowed = True
        self.shared.add("PR")
        self.assertEqual(self.subject.get_regeneration_target("Open Work Orders", "PR"), {"eligible": False})
        self.shared.clear()
        self.pr.owner, self.native_allowed = "other", False
        self.assertEqual(self.subject.get_regeneration_target("Open Work Orders", "PR"), {"eligible": False})
        self.pr.owner, self.native_allowed = "actor", True
        self.artifacts.capture.side_effect = lambda *args, **kwargs: setattr(self.pr, "filters", "{}")
        self.assertEqual(self.subject.get_regeneration_target("Open Work Orders", "PR"), {"eligible": False})

    def test_regeneration_target_ordinary_none_and_same_canonical_custom_allowed(self):
        self.assertEqual(self.subject.get_regeneration_target("Other Report", "PR"), {"eligible": False})
        self.artifacts.capture.assert_not_called()
        self.assertEqual(self.subject.get_regeneration_target("Custom Work Orders", "PR"),
                         {"eligible": True, "name": "PR"})

    def test_regeneration_same_nonce_retry_inserts_once_not_legacy_native_queue_match(self):
        nonce = "123e4567-e89b-42d3-a456-426614174000"
        self.doc("LEGACY-NATIVE-QUEUED").status = "Queued"
        self.native.get_reports_in_queued_state.return_value = [Row(name="LEGACY-NATIVE-QUEUED")]
        first = self.subject.regenerate_prepared_report("PR", nonce)
        second = self.subject.regenerate_prepared_report("PR", nonce)
        self.assertEqual(first, second)
        self.assertNotEqual(first["name"], "LEGACY-NATIVE-QUEUED")
        self.assertEqual(sum(event[:2] == ("save", "Prepared Report") for event in self.events), 1)
        self.native.get_reports_in_queued_state.assert_not_called()

    def test_regeneration_nonce_other_actor_old_identity_or_hidden_source_cannot_reuse_row(self):
        nonce = "123e4567-e89b-42d3-a456-426614174000"
        result = self.subject.regenerate_prepared_report("PR", nonce)
        own = self.docs["Prepared Report", result["name"]]
        other = self.doc("OTHER-OLD")
        self.assertNotEqual(self.subject._regeneration_name(other, "actor", nonce), own.name)
        self.assertNotEqual(self.subject._regeneration_name(self.pr, "other", nonce), own.name)
        self.source_allowed = False
        with self.assertRaises(PermissionError):
            self.subject.regenerate_prepared_report("PR", nonce)
        self.assertEqual(own.status, "Queued")
        self.source_allowed = True
        own.owner = "forged-other-user"
        with self.assertRaises(PermissionError):
            self.subject.regenerate_prepared_report("PR", nonce)

    def test_regeneration_existing_completed_requires_receipt_failed_not_new_nonce_implicitly(self):
        nonce = "123e4567-e89b-42d3-a456-426614174000"
        name = self.subject.regenerate_prepared_report("PR", nonce)["name"]
        new = self.docs["Prepared Report", name]
        new.status = "Completed"
        with self.assertRaises(PermissionError):
            self.subject.regenerate_prepared_report("PR", nonce)
        new.status, new.error_message = "Error", self.subject.GENERATION_ERROR
        with self.assertRaises(PermissionError):
            self.subject.regenerate_prepared_report("PR", nonce)
        self.assertEqual(sum(event[:2] == ("save", "Prepared Report") for event in self.events), 1)

    def test_regeneration_invalid_nonce_never_names_or_inserts_and_collision_verifies_exact_existing(self):
        for nonce in ("", "not-a-uuid", "grant-read"):
            with self.assertRaises(PermissionError):
                self.subject.regenerate_prepared_report("PR", nonce)
        nonce = "123e4567-e89b-42d3-a456-426614174000"
        name = self.subject._regeneration_name(self.pr, "actor", nonce)
        original = self.fr.db.exists.side_effect
        self.doc(name).status = "Queued"
        checks = []
        def race(dt, value):
            if dt == "Prepared Report" and value == name:
                checks.append(1)
                return len(checks) > 1
            return original(dt, value)
        self.fr.db.exists.side_effect = race
        self.assertEqual(self.subject.regenerate_prepared_report("PR", nonce), {"name": name})
        self.assertEqual(sum(event[:2] == ("save", "Prepared Report") for event in self.events), 0)


if __name__ == "__main__":
    unittest.main()
