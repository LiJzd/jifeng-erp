"""Receipt/frame models with real report guard code, not native queue/DB tests."""

from contextvars import ContextVar
import ast
import copy
from datetime import date
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[1]


class Doc(dict):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self.get(name)

    def __setattr__(self, name, value):
        self[name] = value

    def as_dict(self):
        return dict(self)

    def is_permitted(self):
        return self.get("permitted", True)

    def has_permission(self, permtype="read", *, debug=False, user=None):
        return self.get("native_allowed", True)


def freeze(value):
    if callable(getattr(value, "as_dict", None)):
        value = value.as_dict()
    if isinstance(value, dict):
        return tuple((key, freeze(item)) for key, item in sorted(value.items()) if not key.startswith("__"))
    if isinstance(value, (tuple, list)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, int) and not isinstance(value, bool):
        return "int", repr(int(value))
    return type(value).__name__, repr(value)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ArtifactModels(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.docs, self.sql_rows, self.checked, self.field_reads = {}, [], [], []
        self.denied_fields, self.shares, self.hidden_names = set(), set(), set()
        self.source_effect = None
        self.native_pr_effect = None
        self.native_pr_calls = []
        self.fr = types.ModuleType("frappe")
        self.fr.session = Doc(user="actor")
        self.fr.PermissionError, self.fr.DoesNotExistError = PermissionError, KeyError
        self.fr.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.fr._dict = Doc
        self.fr.get_doc = self.get_doc
        self.fr.get_all = self.get_all
        self.fr.get_list = self.get_list
        self.fr.has_permission = self.native_permission
        self.fr.db = Doc(sql=lambda *args, **kwargs: copy.deepcopy(self.sql_rows),
                         exists=self.exists)
        source = types.ModuleType("jifeng_erp.sample_source_permissions")
        source.PARENTS = frozenset({"Jifeng Sample Order", "Quotation", "Sales Order", "Work Order", "Jifeng Flow Order"})
        source._AUTHORIZING = ContextVar("artifact_model_auth", default=frozenset())
        source._key = lambda doc, user: (doc.doctype, doc.name, user)
        source._reference = lambda dt, name: copy.deepcopy(self.docs.get((dt, name)))
        source._scope = lambda doc: bool(doc.get("protected"))
        source._native_read = lambda doc, user: self.require(doc.get("readable", True))
        source._child_parent = self.child_parent
        source._check = self.source_check
        samples = types.ModuleType("jifeng_erp.sample_orders")
        samples._freeze = freeze
        quantity = types.ModuleType("jifeng_erp.flow_quantity")
        quantity.check_quantity_fields = self.check_fields
        queries = types.ModuleType("jifeng_erp.sample_source_queries")
        queries.COVERED = source.PARENTS | {"Quotation Item", "Work Order Item"}
        files = types.ModuleType("jifeng_erp.sample_source_report_files")
        files.canonical_file_identity, files.check_parent_file = self.canonical, self.parent_file
        prepared = types.ModuleType("jifeng_erp.sample_source_prepared_reports")
        prepared.frappe = self.fr
        prepared.PREPARED = "Prepared Report"
        prepared._NATIVE_GUARDS = ContextVar("artifact_real_prepared_native_guard", default=frozenset())
        # Execute B's actual narrow primitive in this model, rather than a
        # fake authorization lambda that cannot expose recursion mistakes.
        nodes = [node for node in ast.parse((ROOT / "jifeng_erp/sample_source_prepared_reports.py").read_bytes()).body
                 if isinstance(node, ast.FunctionDef) and node.name == "_native_permission"]
        self.assertEqual(len(nodes), 1)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "<actual-B-native-permission>", "exec"),
             vars(prepared))
        self.prepared = prepared
        app = types.ModuleType("jifeng_erp")
        app.__path__ = [str(ROOT / "jifeng_erp")]
        app.sample_source_permissions = source
        native_doc = types.ModuleType("frappe.model.document")
        native_doc.Document = Doc
        self.modules = {"frappe": self.fr, "frappe.model.document": native_doc, "jifeng_erp": app,
                        "jifeng_erp.sample_orders": samples, "jifeng_erp.flow_quantity": quantity,
                        "jifeng_erp.sample_source_permissions": source, "jifeng_erp.sample_source_queries": queries,
                        "jifeng_erp.sample_source_report_files": files,
                        "jifeng_erp.sample_source_prepared_reports": prepared}
        context = patch.dict(sys.modules, self.modules)
        context.start()
        self.addCleanup(context.stop)
        self.reports = load("jifeng_erp.sample_source_reports", "jifeng_erp/sample_source_reports.py")
        app.sample_source_reports = self.reports
        self.subject = load("jifeng_erp.sample_source_report_artifacts", "jifeng_erp/sample_source_report_artifacts.py")
        app.sample_source_report_artifacts = self.subject
        self.controller = load("artifact_controller_under_test",
                               "jifeng_erp/jifeng_erp/doctype/jifeng_report_artifact/jifeng_report_artifact.py")
        self.source = source
        self.company = self.doc("Company", "COMPANY")
        self.work = self.doc("Work Order", "WO", company="COMPANY", protected=True,
                             readable=True, qty=3, produced_qty=0, docstatus=1, status="Not Started",
                             production_item="FG", creation="created", modified="v1")
        self.sql_rows = [Doc(work_order="WO")]
        self.report()
        self.pr = self.doc("Prepared Report", "PR", owner="actor", report_name="Open Work Orders",
                           filters='{"company":"COMPANY"}', status="Started")

    def require(self, condition):
        if not condition:
            raise PermissionError("MODEL PRIVATE NAME")

    def doc(self, dt, name, **values):
        doc = Doc(doctype=dt, name=name, **values)
        self.docs[dt, name] = doc
        return doc

    def exists(self, dt, name):
        return bool(self.get_all(dt, filters=name)) if isinstance(name, dict) else (dt, name) in self.docs

    def native_permission(self, dt, ptype, *, doc=None, user=None, **kwargs):
        if dt == "Prepared Report":
            key = (doc.name, ptype, user)
            if key not in self.prepared._NATIVE_GUARDS.get():
                raise RuntimeError("unguarded composite Prepared Report hook")
            self.native_pr_calls.append(key)
            if self.native_pr_effect:
                self.native_pr_effect()
        return dt not in self.denied_fields and (doc is None or doc.has_permission(ptype, user=user))

    def report(self, name="Open Work Orders"):
        query = "MODEL FIXED QUERY " + name
        self.reports.REPORT_SPECS[name] = {**self.reports.REPORT_SPECS[name],
                                          "query_sha256": hashlib.sha256(query.encode()).hexdigest()}
        return self.doc("Report", name, report_name=name, report_type="Query Report", query=query,
                        ref_doctype="Work Order", module="Manufacturing", is_standard="Yes")

    def get_doc(self, dt=None, name=None, **kwargs):
        if isinstance(dt, dict):
            values = copy.deepcopy(dt)
            if values["doctype"] == self.subject.DOCTYPE:
                doc = self.controller.JifengReportArtifact(values)
                def insert(ignore_permissions=None, set_name=None):
                    self.assertTrue(ignore_permissions)
                    self.assertEqual(set_name, doc.name)
                    self.assertTrue(doc.has_permission("create"))
                    doc.before_insert()
                    doc.before_validate()
                    doc.validate()
                    self.docs[doc.doctype, doc.name] = copy.deepcopy(doc)
                    return doc
                object.__setattr__(doc, "insert", insert)
                return doc
            return Doc(values)
        return copy.deepcopy(self.docs[dt, name])

    def get_all(self, dt, *, filters=None, fields=None, pluck=None, **kwargs):
        rows = []
        for (doctype, _name), doc in self.docs.items():
            if dt != doctype:
                continue
            matched = True
            for key, value in (filters or {}).items():
                actual = doc.get(key)
                matched &= (actual in value[1] if isinstance(value, list) and value[0] == "in"
                            else actual == value)
            if matched:
                rows.append(doc)
        rows.sort(key=lambda row: row.name)
        if pluck:
            return [row.get(pluck) for row in rows]
        return [Doc({key: row.get(key) for key in fields}) if fields else copy.deepcopy(row) for row in rows]

    def get_list(self, dt, *, filters, fields, as_list):
        self.assertEqual(as_list, 1)
        self.field_reads.append((dt, copy.deepcopy(filters), fields))
        return [[row.get(key) for key in fields]
                for row in self.get_all(dt, filters=filters) if row.name not in self.hidden_names]

    def source_check(self, doc, user):
        self.checked.append((doc.doctype, doc.name, user))
        self.source._native_read(doc, user)
        self.reports.observe_dependency(self.docs["Company", doc.company], fields={"name"}, permission="select")
        for key in doc.get("dependencies", []):
            dependency = self.docs[tuple(key)]
            self.reports.observe_dependency(dependency, fields=set(dependency), permission="read")
        if self.source_effect:
            self.source_effect()
        self.reports.observe_dependency(doc, completed_source=True)

    def child_parent(self, child):
        parent = self.source._reference(child.parenttype, child.parent)
        self.require(parent and child.parentfield == "items"
                     and any(row.name == child.name for row in parent.get("items", [])))
        return parent

    def check_fields(self, dt, fields, **kwargs):
        self.require(not any((dt, field) in self.denied_fields for field in fields))

    def canonical(self, doc):
        if doc.get("is_folder") or str(doc.get("file_url") or "").startswith("https://"):
            return None
        if not doc.get("file_url"):
            return None
        path = self.path / Path(doc.file_url).name
        aliases = [{key: row.get(key) for key in self.subject._FILE_FIELDS}
                   for (dt, _name), row in sorted(self.docs.items())
                   if dt == "File" and row.get("file_url") == doc.file_url]
        return {"path": str(path), "path_sha256": hashlib.sha256(str(path).encode()).hexdigest(),
                "private": bool(doc.get("is_private") and doc.file_url.startswith("/private/files/")),
                "aliases": aliases}

    def parent_file(self, doc):
        identity = self.canonical(doc)
        self.require(identity and identity["private"])
        self.require(not any(alias["name"] in self.shares for alias in identity["aliases"]))
        content = Path(identity["path"]).read_bytes()
        roots = self.subject.bound_roots(doc)
        for root in roots:
            self.subject.check_artifact(root, content=content)
        return Doc(signature=self.subject._digest(identity), path=identity["path"], content=content)

    def persist_file(self, frame, *, payload=b'{"result":[],"chart":{"data":[]}}', file_name="report.json.gz", content=None):
        content = gzip.compress(payload) if content is None else content
        file = self.subject.new_pending_file(frame, payload=payload, content=content, file_name=file_name)
        file.name = "FILE-" + frame.generation_id
        file.file_url = "/private/files/" + file.file_name
        file.is_folder, file.attached_to_field = 0, None
        Path(self.canonical(file)["path"]).write_bytes(content)
        persisted = copy.deepcopy(file)
        persisted.pop("content", None)
        self.docs["File", file.name] = persisted
        return file

    def make_json(self):
        with self.subject.generation("Open Work Orders", {"company": "COMPANY"}, prepared_report="PR") as frame:
            file = self.persist_file(frame)
            artifact = self.subject.seal(frame, file)
        return artifact, file

    def test_recursive_custom_chain_exact_output_definition_and_native_saved_columns(self):
        self.doc("Report", "C1", report_name="C1", report_type="Custom Report", reference_report="C2",
                 json=json.dumps({"columns": [{"fieldname": "qty"}], "filters": {"company": "COMPANY"}}))
        self.doc("Report", "C2", report_name="C2", report_type="Custom Report", reference_report="Open Work Orders", json="{}")
        resolved = self.subject.resolve_report("C1")
        self.assertEqual((resolved.requested_name, resolved.canonical_name), ("C1", "Open Work Orders"))
        self.assertEqual(resolved.report.custom_columns, [{"fieldname": "qty"}])
        self.assertEqual(len(resolved.chain[0]), 3)

    def test_ordinary_report_returns_none_and_known_definition_change_denied(self):
        self.doc("Report", "Ordinary", report_type="Query Report")
        self.assertIsNone(self.subject.resolve_report("Ordinary"))
        self.docs["Report", "Open Work Orders"].query += " changed"
        with self.assertRaises(PermissionError):
            self.subject.resolve_report("Open Work Orders")

    def test_missing_cycle_disabled_and_native_ref_permission_fail_closed(self):
        self.doc("Report", "Cycle", report_type="Custom Report", reference_report="Cycle")
        for name in ("Missing", "Cycle"):
            with self.assertRaises(PermissionError):
                self.subject.resolve_report(name)
        self.docs["Report", "Open Work Orders"].disabled = 1
        with self.assertRaises(PermissionError):
            self.subject.resolve_report("Open Work Orders")

    def test_capture_freezes_ordinary_contributors_without_granting_new_read(self):
        self.work.protected, self.work.readable = False, False
        before = self.subject.capture("Open Work Orders", {})
        self.assertFalse(self.checked)
        self.work.qty = 4
        self.assertNotEqual(before.digest, self.subject.capture("Open Work Orders", {}).digest)
        self.work.protected = True
        with self.assertRaises(PermissionError):
            self.subject.capture("Open Work Orders", {})

    def test_raw_join_multiplicity_and_membership_frozen_but_row_order_irrelevant(self):
        self.doc("Work Order", "WO2", company="COMPANY", protected=False, qty=2)
        self.sql_rows = [Doc(work_order="WO"), Doc(work_order="WO2"), Doc(work_order="WO")]
        before = self.subject.capture("Open Work Orders", {})
        self.sql_rows.reverse()
        self.assertEqual(before.digest, self.subject.capture("Open Work Orders", {}).digest)
        self.sql_rows.pop()
        self.assertNotEqual(before.digest, self.subject.capture("Open Work Orders", {}).digest)

    def test_actual_source_master_and_observe_only_exists_states_bound(self):
        self.doc("BOM", "BOM", qty=3)
        self.work.dependencies = [("BOM", "BOM")]
        before = self.subject.capture("Open Work Orders", {})
        self.docs["BOM", "BOM"].qty = 4
        self.assertNotEqual(before.digest, self.subject.capture("Open Work Orders", {}).digest)
        self.doc("Stock Entry", "STE", work_order="WO", docstatus=0)
        self.assertNotEqual(before.digest, self.subject.capture("Open Work Orders", {}).digest)

    def test_execution_exit_rechecks_all_sources_and_cleans_frame_on_failure(self):
        with self.assertRaises(PermissionError):
            with self.subject.generation("Open Work Orders", {}, artifact_kind="Execution"):
                self.work.qty = 9
        self.assertIsNone(self.subject.current_frame())
        with self.subject.generation("Open Work Orders", {}, artifact_kind="Execution") as frame:
            self.assertIs(self.subject.current_frame(), frame)
        self.assertIsNone(self.subject.current_frame())

    def test_execution_cannot_write_and_persistent_file_cannot_exit_unsealed(self):
        with self.subject.generation("Open Work Orders", {}, artifact_kind="Execution") as frame:
            with self.assertRaises(PermissionError):
                self.subject.new_pending_file(frame, payload=b"x", content=b"x", file_name="x.csv")
        with self.assertRaises(PermissionError):
            with self.subject.generation("Open Work Orders", {"company": "COMPANY"}, prepared_report="PR") as frame:
                self.persist_file(frame)

    def test_registered_file_exact_object_bytes_private_and_binding_only(self):
        with self.subject.generation("Open Work Orders", {"company": "COMPANY"}, prepared_report="PR") as frame:
            file = self.persist_file(frame)
            self.assertTrue(self.subject.authorize_file_write(file))
            self.assertFalse(self.subject.authorize_file_write(copy.deepcopy(file)))
            for key, value in (("is_private", 0), ("attached_to_name", "Other"), ("content", b"wrong"),
                               ("owner", "other")):
                old = file[key]
                file[key] = value
                with self.assertRaises(PermissionError):
                    self.subject.authorize_file_write(file)
                file[key] = old
            self.subject.seal(frame, file)
            self.assertFalse(self.subject.authorize_file_write(file))
            self.assertIsNone(file.content)

    def test_seal_exact_controller_create_and_consume_payload_chart_bytes(self):
        artifact, file = self.make_json()
        self.assertFalse(self.subject.artifact_has_permission(artifact, "read"))
        self.assertEqual(self.subject.artifact_query_conditions(), "1=0")
        raw = Path(self.canonical(file)["path"]).read_bytes()
        proof = self.subject.check_artifact(artifact.name, content=raw)
        self.assertTrue(proof.content_verified)
        self.assertEqual(json.loads(proof.payload)["chart"], {"data": []})
        self.assertEqual(proof.name, artifact.name)
        with self.assertRaises(PermissionError):
            self.subject.check_artifact(artifact.name, content=raw + b"changed")

    def test_old_without_receipt_regenerate_and_controller_ignore_permissions_cannot_mutate(self):
        with self.assertRaisesRegex(PermissionError, self.subject.LEGACY_MESSAGE):
            self.subject.check_artifact("legacy")
        artifact, _file = self.make_json()
        for method in (artifact.before_validate, artifact.validate, artifact.on_trash,
                       artifact.before_rename, lambda: artifact.validate_share(Doc())):
            with self.assertRaises(PermissionError):
                method()
        self.assertFalse(artifact.has_permission("write"))

    def test_prepared_status_timing_changes_allowed_binding_edits_denied(self):
        with self.subject.generation("Open Work Orders", {"company": "COMPANY"}, prepared_report="PR") as frame:
            file = self.persist_file(frame)
            self.subject.seal(frame, file)
            self.pr.status, self.pr.modified, self.pr.execution_time = "Completed", "now", 2.4
        artifact = frame.sealed_artifact
        self.subject.check_artifact(artifact.name)
        self.pr.filters = '{"company":"Different"}'
        with self.assertRaises(PermissionError):
            self.subject.check_artifact(artifact.name)

    def test_ordinary_then_protected_or_deleted_contributor_refuses_old_snapshot(self):
        self.work.protected = False
        artifact, _file = self.make_json()
        self.work.protected = True
        with self.assertRaises(PermissionError):
            self.subject.check_artifact(artifact.name)
        self.work.protected = False
        del self.docs["Work Order", "WO"]
        with self.assertRaises(PermissionError):
            self.subject.check_artifact(artifact.name)

    def test_definition_roles_and_custom_output_options_bound_timing_excluded(self):
        artifact, _file = self.make_json()
        report = self.docs["Report", "Open Work Orders"]
        report.modified, report.prepared_report, report.prepared_report_time = "changed", 1, 0.4
        self.subject.check_artifact(artifact.name)
        report.roles = [{"role": "Other"}]
        with self.assertRaises(PermissionError):
            self.subject.check_artifact(artifact.name)

    def test_custom_field_native_up_omitted_names_and_ordinary_raw_state_retained(self):
        self.doc("Customer", "Visible", phone="a")
        self.doc("Customer", "Hidden", phone="b")
        self.hidden_names.add("Hidden")
        with self.subject.generation("Open Work Orders", {}, artifact_kind="Execution") as frame:
            values = self.subject.record_extra_dependency(frame, doctype="Customer", field="phone",
                                                           names=["Visible", "Hidden", "Missing", "Visible"])
            self.assertEqual(values, {"Visible": "a"})
            self.assertEqual(next(iter(frame.observations.values()))["names"], ["Hidden", "Missing", "Visible"])
        with self.assertRaises(PermissionError):
            with self.subject.generation("Open Work Orders", {}, artifact_kind="Execution") as frame:
                self.subject.record_extra_dependency(frame, doctype="Customer", field="phone", names=["Hidden"])
                self.docs["Customer", "Hidden"].phone = "changed"

    def test_custom_field_protected_parent_field_and_child_identity_mask_checked(self):
        self.work.secret = "value"
        self.denied_fields.add(("Work Order", "secret"))
        with self.subject.generation("Open Work Orders", {}, artifact_kind="Execution") as frame:
            with self.assertRaises(PermissionError):
                self.subject.record_extra_dependency(frame, doctype="Work Order", field="secret", names=["WO"])
        self.denied_fields.clear()
        child = self.doc("Work Order Item", "ROW", parenttype="Work Order", parent="WO", parentfield="items", amount=9)
        self.work.items = [copy.deepcopy(child)]
        self.denied_fields.add(("Work Order Item", "amount"))
        with self.subject.generation("Open Work Orders", {}, artifact_kind="Execution") as frame:
            with self.assertRaises(PermissionError):
                self.subject.record_extra_dependency(frame, doctype="Work Order Item", field="amount", names=["ROW"])
        self.denied_fields.clear()
        child.parentfield = "alien"
        with self.subject.generation("Open Work Orders", {}, artifact_kind="Execution") as frame:
            with self.assertRaises(PermissionError):
                self.subject.record_extra_dependency(frame, doctype="Work Order Item", field="amount", names=["ROW"])

    def test_no_completed_authorization_or_observation_crosses_next_request(self):
        with self.subject.generation("Open Work Orders", {}, artifact_kind="Execution"):
            pass
        self.work.readable = False
        with self.assertRaises(PermissionError):
            with self.subject.generation("Open Work Orders", {}, artifact_kind="Execution"):
                pass
        self.assertIsNone(self.subject.current_frame())
        self.assertIsNone(self.reports._OBSERVING.get())

    def test_bound_roots_union_current_persisted_and_canonical_alias_remote_ordinary(self):
        artifact, file = self.make_json()
        changed = copy.deepcopy(file)
        changed.file_url = "https://remote/ordinary"
        self.assertEqual(self.subject.bound_roots(changed), [artifact.name])
        alias = self.doc("File", "ALIAS", file_url=file.file_url, file_name="alias", is_private=1)
        self.assertEqual(self.subject.bound_roots(alias), [artifact.name])
        self.assertTrue(self.subject.check_artifact(artifact.name, file_doc=alias))

    def test_alias_change_during_consume_fails_and_recursion_frame_resets(self):
        artifact, file = self.make_json()
        native = self.subject.capture
        calls = 0
        def capture(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.doc("File", "ALIAS", file_url=file.file_url, is_private=1)
            return native(*args, **kwargs)
        with patch.object(self.subject, "capture", capture):
            with self.assertRaises(PermissionError):
                self.subject.check_artifact(artifact.name)
        self.assertFalse(self.subject._CONSUMING.get())

    def test_receipt_metadata_kind_or_source_parent_not_client_editable(self):
        artifact, _file = self.make_json()
        stored = self.docs[self.subject.DOCTYPE, artifact.name]
        stored.artifact_kind = "Export"
        with self.assertRaises(PermissionError):
            self.subject.check_artifact(artifact.name)

    def test_dependency_native_date_binding_serializes_exact_state(self):
        owner = ("Work Order", "WO")
        self.doc("Fiscal Year", "FY", year_start_date=date(2026, 1, 1))
        dependency = self.reports.ReportDependency(owner, "Fiscal Year", name="FY",
                                                  fields=("year_start_date",),
                                                  bindings={"year_start_date": date(2026, 1, 1)})
        original = self.reports.REPORT_SPECS["Open Work Orders"]
        scope = self.reports.ReportScope(rows=[Doc(work_order="WO")], parents=[owner], dependencies=[dependency])
        self.reports.REPORT_SPECS["Open Work Orders"] = {**original, "selector": lambda filters: scope}
        artifact, _file = self.make_json()
        self.assertTrue(self.subject.check_artifact(artifact.name))

    def test_authorized_other_consumer_and_added_private_alias_do_not_poison_parent_receipt(self):
        parent, file = self.make_json()
        with self.subject.generation("Open Work Orders", {}, artifact_kind="Export", source_artifact=parent.name) as frame:
            export = self.persist_file(frame, payload=b"{}", content=b"qty\n3\n", file_name="report.csv")
            artifact = self.subject.seal(frame, export)
        self.assertEqual(export.folder, "Home/Exported Reports")
        self.fr.session.user = "other-authorized-reader"
        self.doc("File", "NEW-PRIVATE-ALIAS", file_url=file.file_url, file_name="alias", is_private=1)
        self.assertTrue(self.subject.check_artifact(artifact.name))

    def test_export_execution_binds_wide_json_parent_and_rejects_parent_share_or_byte_change(self):
        parent, parent_file = self.make_json()
        with self.subject.generation("Open Work Orders", {}, artifact_kind="Execution", source_artifact=parent.name) as frame:
            self.assertEqual(frame._parent["name"], parent.name)
        self.shares.add(parent_file.name)
        with self.assertRaises(PermissionError):
            with self.subject.generation("Open Work Orders", {}, artifact_kind="Execution", source_artifact=parent.name):
                pass
        self.shares.clear()
        Path(self.canonical(parent_file)["path"]).write_bytes(b"changed")
        with self.assertRaises(PermissionError):
            with self.subject.generation("Open Work Orders", {}, artifact_kind="Execution", source_artifact=parent.name):
                pass

    def test_csv_parent_same_pr_export_allows_same_canonical_custom_request(self):
        parent, _file = self.make_json()
        with self.subject.generation("Open Work Orders", {"company": "COMPANY"}, prepared_report="PR",
                                     artifact_kind="CSV", source_artifact=parent.name) as frame:
            file = self.persist_file(frame, payload=b'{"result":[]}', content=b"qty\n3\n", file_name="report.csv")
            child = self.subject.seal(frame, file)
        self.assertTrue(self.subject.check_artifact(child.name).receipt["source_parent"])
        self.doc("Report", "Custom", report_name="Custom", report_type="Custom Report",
                 reference_report="Open Work Orders", json="{}")
        with self.subject.generation("Custom", {}, artifact_kind="Export", source_artifact=parent.name) as frame:
            file = self.persist_file(frame, payload=b'{"result":[]}', content=b"qty\n3\n", file_name="custom.csv")
            child = self.subject.seal(frame, file)
        self.assertEqual(self.subject.check_artifact(child.name).receipt["source_parent"]["name"], parent.name)

    def test_authorized_non_creator_csv_preserves_native_pr_and_complete_parent(self):
        parent, _file = self.make_json()
        self.fr.session.user = "other-authorized-reader"
        with self.subject.generation("Open Work Orders", {"company": "COMPANY"}, prepared_report="PR",
                                     artifact_kind="CSV", source_artifact=parent.name) as frame:
            self.assertEqual(frame.prepared_binding["owner"], "actor")
            self.assertEqual(frame.user, "other-authorized-reader")
            file = self.persist_file(frame, payload=b'{"result":[]}', content=b"qty\n3\n", file_name="report.csv")
            child = self.subject.seal(frame, file)
        self.assertEqual(child.owner, "other-authorized-reader")
        self.assertEqual(self.subject.check_artifact(child.name).receipt["source_parent"]["name"], parent.name)
        self.assertTrue(self.native_pr_calls)
        self.assertEqual(set(self.native_pr_calls), {("PR", "read", "other-authorized-reader")})
        self.assertFalse(self.prepared._NATIVE_GUARDS.get())
        self.assertIsNone(self.subject.current_frame())

    def test_non_creator_csv_requires_native_read_unshared_full_source_and_same_pr(self):
        parent, _file = self.make_json()
        self.fr.session.user = "other-authorized-reader"

        def attempt(prepared="PR"):
            with self.subject.generation("Open Work Orders", {"company": "COMPANY"}, prepared_report=prepared,
                                         artifact_kind="CSV", source_artifact=parent.name):
                self.fail("denied CSV entered its generation body")

        self.pr.native_allowed = False
        with self.assertRaises(PermissionError):
            attempt()
        self.pr.native_allowed = True
        self.native_pr_effect = lambda: self.doc("DocShare", "PR-SHARE", share_doctype="Prepared Report", share_name="PR")
        with self.assertRaises(PermissionError):
            attempt()
        self.native_pr_effect = None
        del self.docs["DocShare", "PR-SHARE"]
        self.denied_fields.add(("Work Order", "qty"))
        with self.assertRaises(PermissionError):
            attempt()
        self.denied_fields.clear()
        self.doc("Prepared Report", "PR2", owner="actor", report_name="Open Work Orders",
                 filters='{"company":"COMPANY"}', status="Completed")
        with self.assertRaises(PermissionError):
            attempt("PR2")
        self.assertIsNone(self.subject.current_frame())
        self.assertFalse(self.prepared._NATIVE_GUARDS.get())

    def test_csv_native_read_revoked_during_generation_denies_and_cleans_real_guard(self):
        parent, _file = self.make_json()
        self.fr.session.user = "other-authorized-reader"
        with self.assertRaises(PermissionError):
            with self.subject.generation("Open Work Orders", {"company": "COMPANY"}, prepared_report="PR",
                                         artifact_kind="CSV", source_artifact=parent.name):
                self.pr.native_allowed = False
        self.assertIsNone(self.subject.current_frame())
        self.assertFalse(self.prepared._NATIVE_GUARDS.get())
        self.pr.native_allowed = True
        with self.subject.generation("Open Work Orders", {"company": "COMPANY"}, prepared_report="PR",
                                     artifact_kind="CSV", source_artifact=parent.name):
            pass
        self.assertFalse(self.prepared._NATIVE_GUARDS.get())

    def test_json_creator_requirement_and_export_explicit_pr_read_gate_remain(self):
        parent, _file = self.make_json()
        self.fr.session.user = "other-authorized-reader"
        with self.assertRaises(PermissionError):
            with self.subject.generation("Open Work Orders", {"company": "COMPANY"}, prepared_report="PR"):
                pass
        self.pr.native_allowed = False
        with self.assertRaises(PermissionError):
            with self.subject.generation("Open Work Orders", {"company": "COMPANY"}, prepared_report="PR",
                                         artifact_kind="Export", source_artifact=parent.name):
                pass
        self.pr.native_allowed = True
        with self.subject.generation("Open Work Orders", {"company": "COMPANY"}, prepared_report="PR",
                                     artifact_kind="Export", source_artifact=parent.name) as frame:
            self.assertEqual(frame._parent["name"], parent.name)
        self.assertFalse(self.prepared._NATIVE_GUARDS.get())

    def test_generation_input_tamper_user_change_and_nested_context_rejected(self):
        with self.assertRaises(PermissionError):
            with self.subject.generation("Open Work Orders", {}, artifact_kind="Execution") as frame:
                frame.filters["company"] = "changed"
        with self.assertRaises(PermissionError):
            with self.subject.generation("Open Work Orders", {}, artifact_kind="Execution"):
                self.fr.session.user = "other"
        self.fr.session.user = "actor"
        with self.subject.generation("Open Work Orders", {}, artifact_kind="Execution"):
            with self.assertRaises(PermissionError):
                with self.subject.generation("Open Work Orders", {}, artifact_kind="Execution"):
                    pass

    def test_native_filename_transformation_registered_before_file_creation(self):
        with self.subject.generation("Open Work Orders", {"company": "COMPANY"}, prepared_report="PR") as frame:
            file = self.persist_file(frame, file_name="custom%?#\\name/report.json.gz")
            self.assertEqual(file.file_name, frame.generation_id + "_custom____name_report.json.gz")
            self.assertTrue(self.subject.authorize_file_write(file))
            self.subject.seal(frame, file)

    def test_schema_retains_exact_ids_without_blocking_native_cleanup_and_forbids_shares(self):
        schema = json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_report_artifact/jifeng_report_artifact.json").read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in schema["fields"]}
        self.assertFalse(schema["permissions"])
        self.assertEqual([fields[name]["fieldtype"] for name in ("report", "prepared_report", "file")], ["Data"] * 3)
        self.assertTrue(fields["file"]["unique"])
        self.assertTrue(fields["receipt"]["ignore_xss_filter"])
        with self.assertRaises(PermissionError):
            self.subject.before_validate_artifact_share(Doc(share_doctype=self.subject.DOCTYPE, share_name="ghost"))
        self.subject.before_validate_artifact_share(Doc(share_doctype="Ordinary", share_name="ordinary"))

    def test_custom_prepared_switch_overrides_canonical_in_both_directions(self):
        leaf = self.docs["Report", "Open Work Orders"]
        custom = self.doc("Report", "Custom", report_type="Custom Report", reference_report=leaf.name,
                          prepared_report=0, json="{}")
        leaf.prepared_report = 1
        self.assertEqual(self.subject.resolve_report(custom.name).report.prepared_report, 0)
        leaf.prepared_report, custom.prepared_report = 0, 1
        self.assertEqual(self.subject.resolve_report(custom.name).report.prepared_report, 1)

    def test_multi_custom_uses_outer_switch_and_signs_custom_not_native_auto_switch(self):
        leaf = self.docs["Report", "Open Work Orders"]
        inner = self.doc("Report", "Inner", report_type="Custom Report", reference_report=leaf.name,
                         prepared_report=1, json="{}")
        outer = self.doc("Report", "Outer", report_type="Custom Report", reference_report=inner.name,
                         prepared_report=0, json="{}")
        before = self.subject.capture(outer.name, {})
        self.assertEqual(before.resolved.report.prepared_report, 0)
        leaf.prepared_report = 1
        self.assertEqual(before.digest, self.subject.capture(outer.name, {}).digest)
        outer.prepared_report = 1
        after = self.subject.capture(outer.name, {})
        self.assertEqual(after.resolved.report.prepared_report, 1)
        self.assertNotEqual(before.digest, after.digest)


if __name__ == "__main__":
    unittest.main()
