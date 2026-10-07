"""Native Engine-shape and source permission regression models; no database."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cost_models = load("cost_models", ROOT / "tests/test_flow_cost_sources.py")
permission_models = load("permission_models", ROOT / "tests/test_sample_source_permissions.py")


class ExactPopulationPermissions(permission_models.SampleSourcePermissionTests):
    def extra_flow(self):
        doc = self.env.get_doc("Jifeng Flow Order", "F")
        doc.name = "F2"
        self.env.put(doc)

    def test_scoped_global_unrelated_parent_and_child_share_still_aborts(self):
        self.extra_flow()
        self.env.put(self.share("Jifeng Flow Order", "F2"))
        with self.assertRaises(PermissionError):
            self.module._query_conditions(doctype="Jifeng Flow Order", candidate_names=("F",))
        self.env.records.pop(("DocShare", "share"))
        work = self.env.get_doc("Work Order", "W")
        work.name = "W2"
        work.operations = [permission_models.Doc(doctype="Work Order Operation", name="W2-OP",
            parent="W2", parenttype="Work Order", parentfield="operations")]
        self.env.put(work)
        self.env.put(self.share("Work Order Operation", "W2-OP"))
        with self.assertRaises(PermissionError):
            self.module._query_conditions(doctype="Work Order", candidate_names=("W",))

    def test_scoped_exact_parent_retains_full_source_and_fresh_denials(self):
        self.extra_flow()
        condition = self.module._query_conditions(doctype="Jifeng Flow Order", candidate_names=("F",))
        self.assertIn("'F'", condition)
        self.assertNotIn("'F2'", condition)
        self.env.hidden.add("I")
        self.assertEqual(self.module._query_conditions(doctype="Jifeng Flow Order", candidate_names=("F",)), "1=0")
        self.env.hidden.clear()
        self.env.masked.add(("Quotation Item", "rate"))
        self.assertEqual(self.module._query_conditions(doctype="Jifeng Flow Order", candidate_names=("F",)), "1=0")
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())


class FixedCostEngine(cost_models.TestFlowCostSources):
    def setUp(self):
        super().setUp()
        self.scopes, self.native_options, self.external_calls, self.script_calls = [], [], [], []
        self.source_callback, self.external_callback = None, None
        self.hooks = {dt: ["source"] for dt in (self.module.FLOW, self.module.WO)}
        class CostMeta(cost_models.Meta):
            def get_table_fields(inner):
                return [field for field in inner.fields if field.fieldtype == "Table"]
        self.frappe.get_meta = lambda kind: CostMeta(self, kind)
        source = types.ModuleType("jifeng_erp.sample_source_permissions")
        def unscoped(user=None, doctype=None):
            self.external_calls.append(("unscoped", doctype))
            return "1=1"
        def scoped(user=None, doctype=None, *, candidate_names=None):
            self.scopes.append((doctype, candidate_names))
            if self.source_callback:
                self.source_callback(doctype, candidate_names)
            return "1=1"
        source.sample_source_query_conditions, source._query_conditions = unscoped, scoped
        def external(user, doctype):
            self.external_calls.append(("external", doctype))
            if self.external_callback:
                self.external_callback(doctype)
            return "external=1"
        self.frappe.get_hooks = lambda name, default: self.hooks
        self.frappe.get_attr = lambda method: {"source": unscoped, "external": external}[method]
        self.frappe.call = lambda function, *args, **kwargs: function(*args, **kwargs)
        environment = self
        class RawCriterion(str):
            pass
        class Query:
            def __init__(inner, engine):
                inner.engine = engine
            def run(inner, as_dict):
                self.assertTrue(as_dict)
                e = inner.engine
                return environment.get_list(e.doctype, filters=e.filters, fields=e.fields,
                    limit_page_length=e.limit, order_by="name asc",
                    **({"parent_doctype": e.parent_doctype} if e.parent_doctype else {}))
        class Engine:
            def get_query(inner, **options):
                self.native_options.append(deepcopy(options))
                inner.doctype, inner.fields = options["table"], options["fields"]
                inner.filters, inner.limit = options["filters"], options["limit"]
                inner.user, inner.parent_doctype = options["user"], options["parent_doctype"]
                inner.permission_doctype = inner.parent_doctype or inner.doctype
                inner.apply_permissions, inner.ignore_user_permissions = not options["ignore_permissions"], options["ignore_user_permissions"]
                inner.get_permission_query_conditions()
                return Query(inner)
            def get_queried_tables(inner):
                return ["tab" + inner.doctype] + (["tab" + inner.parent_doctype] if inner.parent_doctype else [])
        native = types.ModuleType("frappe.database.query")
        native.Engine, native.RawCriterion = Engine, RawCriterion
        scripts = types.ModuleType("frappe.core.doctype.server_script.server_script_utils")
        scripts.get_server_script_map = lambda: {"permission_query": {self.module.FLOW: "SCRIPT"}} if self.script_calls else {}
        adapter = load("flow_cost_scoped_query_under_test", ROOT / "jifeng_erp/flow_cost_scoped_query.py")
        self.adapter = adapter
        self.module.record_list = adapter.record_list
        modules = patch.dict(sys.modules, {"frappe.database.query": native,
            "frappe.core.doctype.server_script.server_script_utils": scripts,
            "jifeng_erp.sample_source_permissions": source})
        modules.start();self.addCleanup(modules.stop)
        import jifeng_erp
        source_attr = patch.object(jifeng_erp, "sample_source_permissions", source, create=True)
        source_attr.start();self.addCleanup(source_attr.stop)
        get_doc = self.frappe.get_doc
        def script_or_doc(kind, name):
            if kind == "Server Script":
                return types.SimpleNamespace(get_permission_query_conditions=lambda user, active_child_tables:
                    self.script_calls.append((user, tuple(active_child_tables))) or "script=1")
            return get_doc(kind, name)
        self.frappe.get_doc = script_or_doc

    def test_scoped_real_cost_shape_keeps_full_finish_and_native_permissions(self):
        self.add(self.module.FLOW, "OTHER", company="JF", item_code="FG")
        self.manufacture()
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual(result["totals"]["material"], 40)
        self.assertTrue(self.scopes)
        self.assertTrue(all(names in (("FLOW",), ("WO",), ()) for _, names in self.scopes))
        self.assertGreater(self.scopes.count((self.module.FLOW, ("FLOW",))), 2)
        self.assertTrue(all(not row["ignore_permissions"] and not row["ignore_user_permissions"]
            and row["db_query_compat"] and row["limit"] == 0 for row in self.native_options))
        self.assertFalse(self.frappe.db.commit.called)

    def test_scoped_unrelated_sources_keep_original_native_get_list(self):
        self.receipt()
        self.module.receipt_costs("FLOW")
        self.assertTrue(any(call.args[0] == self.module.SCR for call in self.frappe.get_list.call_args_list))
        self.assertFalse(any(row["table"] == self.module.SCR for row in self.native_options))

    def test_scoped_parent_only_raw_and_native_keep_corrupt_identity_visible(self):
        self.documents["Jifeng Material Line", "MAT"].parenttype = "Work Order"
        with self.assertRaises(ValueError):
            self.module.posted_flow_costs("FLOW")
        child_queries = [row for row in self.native_options if row["table"] == "Jifeng Material Line"]
        self.assertEqual(child_queries[0]["filters"], {"parent": "FLOW"})

    def test_scoped_more_than_thousand_children_never_silently_truncate(self):
        for i in range(1002):
            self.child(self.module.FLOW, "FLOW", "materials", "MAT-" + str(i), item_code="RM")
        rows = self.adapter.record_list("Jifeng Material Line", filters={"parent": "FLOW"},
            fields=["name", "parent"], parenttype=self.module.FLOW)
        self.assertEqual(len(rows), 1003)

    def test_scoped_hook_position_freshness_and_nested_calls_do_not_inherit_population(self):
        self.hooks[self.module.FLOW] = ["external", "source"]
        source = sys.modules["jifeng_erp.sample_source_permissions"]
        self.external_callback = lambda kind: source.sample_source_query_conditions(user="native-stock-reader", doctype=kind)
        self.script_calls.append("enabled")
        self.module.posted_flow_costs("FLOW")
        self.assertIn(("unscoped", self.module.FLOW), self.external_calls)
        self.assertTrue(any(isinstance(row, tuple) for row in self.script_calls))
        self.assertFalse(hasattr(self.adapter, "_QUERY_CANDIDATES"))

    def test_scoped_late_required_field_mask_refuses_whole_result(self):
        self.receipt()
        calls = 0
        def revoke(kind, names):
            nonlocal calls
            calls += 1
            if calls == 4:
                self.hidden[self.module.FLOW] = {"company"}
        self.source_callback = revoke
        with self.assertRaises(PermissionError):
            self.module.posted_flow_costs("FLOW")

    def test_scoped_guest_actor_swap_and_nonexact_population_are_rejected(self):
        for filters in ({}, {"name": ["in", ["FLOW"]]}, {"company": "JF"}):
            with self.assertRaises(PermissionError):
                self.adapter.record_list(self.module.FLOW, filters=filters, fields=["name"])
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.adapter.record_list(self.module.FLOW, filters={"name": "FLOW"}, fields=["name"])
        self.frappe.session.user = "native-stock-reader"
        self.hooks[self.module.FLOW] = ["external", "source"]
        self.external_callback = lambda kind: self.frappe.session.update(user="changed-user")
        with self.assertRaises(PermissionError):
            self.adapter.record_list(self.module.FLOW, filters={"name": "FLOW"}, fields=["name"])


if __name__ == "__main__":
    suite = unittest.TestSuite()
    for cls in (ExactPopulationPermissions, FixedCostEngine):
        for name in sorted(cls.__dict__):
            if name.startswith("test_scoped_"):
                suite.addTest(cls(name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
