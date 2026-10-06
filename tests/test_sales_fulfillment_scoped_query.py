"""Local fake models only: no Frappe/native import, database or API call."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import re
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


permission_models = load("permission_models", ROOT / "tests/test_sample_source_permissions.py")
graph_models = load("graph_models", ROOT / "tests/test_sales_fulfillment_workbench.py")


class PermissionPopulationModels(permission_models.SampleSourcePermissionTests):
    def extra_order(self):
        order = self.env.get_doc("Sales Order", "SO")
        order.name = "SO2"
        for row in order.get("items"):
            row.parent, row.name = "SO2", "SO2-ROW"
        self.env.put(order)

    def test_candidate_exact_gate_does_not_authorize_other_so(self):
        self.extra_order()
        condition = self.module._query_conditions(doctype="Sales Order", candidate_names=("SO",))
        self.assertIn("'SO'", condition)
        self.assertNotIn("'SO2'", condition)
        self.assertEqual(sum(c[0] == "sales_quote_history" for c in self.env.calls), 1)
        self.assertEqual(sum(c[0] == "authorize" for c in self.env.calls), 2)
        self.env.calls.clear()
        condition = self.module.sample_source_query_conditions(doctype="Sales Order")
        self.assertIn("'SO2'", condition)
        self.assertEqual(sum(c[0] == "sales_quote_history" for c in self.env.calls), 2)

    def test_candidate_unrelated_share_still_aborts(self):
        self.extra_order()
        self.env.put(self.share("Sales Order", "SO2"))
        with self.assertRaises(PermissionError):
            self.module._query_conditions(doctype="Sales Order", candidate_names=("SO",))

    def test_candidate_empty_still_checks_share_and_does_no_source_allow(self):
        self.assertEqual(self.module._query_conditions(doctype="Sales Order", candidate_names=()), "1=0")
        self.assertFalse(any(c[0] == "sales_quote_history" for c in self.env.calls))
        self.env.put(self.share("Sales Order", "SO"))
        with self.assertRaises(PermissionError):
            self.module._query_conditions(doctype="Sales Order", candidate_names=())

    def test_candidate_required_source_and_mask_denials_keep_full_gate(self):
        for alteration in ("source", "mask"):
            with self.subTest(alteration=alteration):
                if alteration == "source":
                    self.env.hidden.add("I")
                else:
                    self.env.masked.add(("Quotation Item", "rate"))
                self.assertEqual(self.module._query_conditions(doctype="Sales Order", candidate_names=("SO",)), "1=0")
                self.env.hidden.clear()
                self.env.masked.clear()
        self.assertEqual(self.module._AUTHORIZING.get(), frozenset())
        self.assertEqual(self.module._CHECKING_DOCUMENTS.get(), ())
        self.assertEqual(self.module._SALES_HISTORY.get(), ())

    def test_candidate_never_reuses_allow_and_actor_is_exact(self):
        self.module._query_conditions(doctype="Sales Order", candidate_names=("SO",))
        self.env.hidden.add("I")
        self.assertEqual(self.module._query_conditions(doctype="Sales Order", candidate_names=("SO",)), "1=0")
        self.assertEqual(self.module._query_conditions(user="other-user", doctype="Sales Order", candidate_names=("SO",)), "1=0")
        self.env.hidden.clear()
        self.assertIn("'SO'", self.module._query_conditions(doctype="Sales Order", candidate_names=("SO",)))

    def test_candidate_selected_parent_keeps_unselected_quote_sample_sources(self):
        sample = self.env.get_doc("Jifeng Sample Order", "S")
        sample.get("items").append(permission_models.child("Jifeng Sample Order Item", "Jifeng Sample Order",
            "S", "S2", item_code="OTHER_ITEM", qty=1, stock_uom="Nos"))
        self.env.put(sample)
        quote = self.env.get_doc("Quotation", "Q")
        row = permission_models.Doc(deepcopy(quote.get("items")[0]))
        row.name, row.item_code, row.jf_sample_order_item = "Q2", "OTHER_ITEM", "S2"
        quote.get("items").append(row)
        self.env.put(quote)
        self.assertIn("'SO'", self.module._query_conditions(doctype="Sales Order", candidate_names=("SO",)))
        self.env.hidden.add("OTHER_ITEM")
        self.assertEqual(self.module._query_conditions(doctype="Sales Order", candidate_names=("SO",)), "1=0")

    def test_candidate_nested_unscoped_pqc_has_full_population(self):
        self.extra_order()
        body = self.env.historical_sales_quote_row
        nested = []
        running = False
        def callback(order, row, quote):
            nonlocal running
            if not running:
                running = True
                # Different protected doctype avoids the native existing active
                # SO recursion condition while testing absence of scoped state.
                nested.append(self.module.sample_source_query_conditions(doctype="Quotation"))
                running = False
            return body(order, row, quote)
        with patch.object(sys.modules["jifeng_erp.sample_quotations"], "_historical_sales_quote_row", callback):
            self.module._query_conditions(doctype="Sales Order", candidate_names=("SO",))
        self.assertTrue(nested)
        self.assertTrue(all("'Q'" in condition for condition in nested))
        self.assertFalse(hasattr(self.module, "_QUERY_CANDIDATES"))


class FixedEngineModels(graph_models.TestSalesFulfillment):
    def setUp(self):
        super().setUp()
        self.scopes, self.native_options, self.external_calls, self.scripts = [], [], [], []
        self.source_callback = None
        self.external_callback = None
        self.hooks = {dt: ["source"] for dt in ("Sales Order", "Jifeng Flow Order", "Work Order")}
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
        self.frappe.get_all = self.project_all
        self.frappe.get_doc = self.script_or_doc
        for parent, (field, child, *_rest) in self.module.TABLES.items():
            self.metas[parent][field].options = child
        environment = self
        class RawCriterion(str):
            pass
        class Query:
            def __init__(inner, engine):
                inner.engine = engine
            def run(inner, as_dict):
                self.assertTrue(as_dict)
                engine = inner.engine
                options = {"parent_doctype": engine.parent_doctype} if engine.parent_doctype else {}
                return environment.get_list(engine.doctype, filters=engine.filters, fields=engine.fields,
                    limit_page_length=engine.limit, order_by="name asc", **options)
        class Engine:
            def get_query(inner, **options):
                self.native_options.append(deepcopy(options))
                inner.doctype, inner.fields = options["table"], options["fields"]
                inner.filters, inner.limit = options["filters"], options["limit"]
                inner.user, inner.parent_doctype = options["user"], options["parent_doctype"]
                inner.permission_doctype = inner.parent_doctype or inner.doctype
                inner.apply_permissions = not options["ignore_permissions"]
                inner.ignore_user_permissions = options["ignore_user_permissions"]
                inner.get_permission_query_conditions()
                return Query(inner)
            def get_queried_tables(inner):
                return ["tab" + inner.doctype] + (["tab" + inner.parent_doctype] if inner.parent_doctype else [])
        native = types.ModuleType("frappe.database.query")
        native.Engine, native.RawCriterion = Engine, RawCriterion
        scripts = types.ModuleType("frappe.core.doctype.server_script.server_script_utils")
        scripts.get_server_script_map = lambda: {"permission_query": {"Sales Order": "SCRIPT"}} if self.scripts else {}
        scoped_module = load("jifeng_erp.sales_fulfillment_scoped_query", ROOT / "jifeng_erp/sales_fulfillment_scoped_query.py")
        self.adapter = scoped_module
        self.modules = patch.dict(sys.modules, {"frappe.database.query": native,
            "frappe.core.doctype.server_script.server_script_utils": scripts,
            "jifeng_erp.sample_source_permissions": source,
            "jifeng_erp.sales_fulfillment_scoped_query": scoped_module})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        import jifeng_erp
        self.source_attr = patch.object(jifeng_erp, "sample_source_permissions", source, create=True)
        self.source_attr.start()
        self.addCleanup(self.source_attr.stop)

    def project_all(self, doctype, filters, fields, limit_page_length, order_by):
        rows = super().get_all(doctype, filters, ["*"], limit_page_length, order_by)
        return rows if fields == ["*"] else [graph_models.Record({f: row.get(f) for f in fields}) for row in rows]

    def script_or_doc(self, doctype, name):
        if doctype == "Server Script":
            return types.SimpleNamespace(get_permission_query_conditions=lambda user, active_child_tables:
                self.scripts.append((user, tuple(active_child_tables))) or "script=1")
        return super().get_doc(doctype, name)

    def test_candidate_graph_keeps_finish_native_options_and_private_scope(self):
        self.flow("F1")
        self.flow("F2", detail="SO-ROW-2")
        result = self.read()
        self.assertEqual(len(result["flows"]), 2)
        flow_scopes = [names for dt, names in self.scopes if dt == self.module.FLOW]
        self.assertIn(("F1", "F2"), flow_scopes)
        self.assertIn(("F1",), flow_scopes)
        self.assertIn(("F2",), flow_scopes)
        self.assertIn((), flow_scopes)  # empty materials do not inherit a parent allow
        self.assertGreater(len(self.scopes), 6)  # finish repeats fresh scopes
        self.assertTrue(all(not o["ignore_permissions"] and not o["ignore_user_permissions"]
                            and o["db_query_compat"] for o in self.native_options))
        self.assertTrue(all(o["user"] == "native-reader" for o in self.native_options))

    def test_candidate_exact_childparent_required(self):
        with self.assertRaises(PermissionError):
            self.adapter.record_list("Sales Order Item", filters={"parent": "SO", "parenttype": "Sales Order",
                "parentfield": "wrong"}, fields=["name"], parenttype="Sales Order", limit=1001, scoped=True)
        self.assertFalse(self.native_options)

    def test_candidate_external_callback_nested_same_doctype_stays_unscoped(self):
        self.hooks["Sales Order"].append("external")
        source = sys.modules["jifeng_erp.sample_source_permissions"]
        self.external_callback = lambda dt: source.sample_source_query_conditions(user="native-reader", doctype=dt)
        self.scripts.append("enabled")
        self.read()
        self.assertIn(("external", "Sales Order"), self.external_calls)
        self.assertIn(("unscoped", "Sales Order"), self.external_calls)
        self.assertTrue(any(isinstance(call, tuple) for call in self.scripts))

    def test_candidate_nested_scoped_same_doctype_owns_distinct_filters(self):
        self.hooks["Sales Order"].append("external")
        self.add("Sales Order", "OTHER", company="JF", docstatus=1)
        inside = False
        def nested(doctype):
            nonlocal inside
            if inside:
                return
            inside = True
            self.adapter.record_list("Sales Order", filters={"name": "OTHER"}, fields=["name"],
                                     limit=1001, scoped=True)
            inside = False
        self.external_callback = nested
        self.adapter.record_list("Sales Order", filters={"name": "SO"}, fields=["name"],
                                 limit=1001, scoped=True)
        self.assertEqual(self.scopes, [("Sales Order", ("SO",)), ("Sales Order", ("OTHER",))])
        self.assertEqual([o["filters"] for o in self.native_options], [{"name": "SO"}, {"name": "OTHER"}])

    def test_candidate_actor_change_in_prior_hook_is_not_an_allow(self):
        self.hooks["Sales Order"] = ["external", "source"]
        self.external_callback = lambda dt: setattr(self.frappe.session, "user", "other-user")
        # The real source helper returns 1=0 on actor mismatch; this adapter
        # model verifies the exact original actor is passed, never the new one.
        actors = []
        original = sys.modules["jifeng_erp.sample_source_permissions"]._query_conditions
        def observe(user=None, doctype=None, candidate_names=None):
            actors.append((user, self.frappe.session.user))
            return original(user=user, doctype=doctype, candidate_names=candidate_names)
        with patch.object(sys.modules["jifeng_erp.sample_source_permissions"], "_query_conditions", observe):
            with self.assertRaises(PermissionError):
                self.read()
        self.assertFalse(actors)

    def test_candidate_after_query_new_membership_is_rejected(self):
        self.flow("F1")
        def add_after_query(filters):
            if "sales_order" in filters:
                self.flow("F2", detail="SO-ROW-2")
        self.list_hooks[self.module.FLOW] = add_after_query
        with self.assertRaises(PermissionError):
            self.read()

    def test_candidate_finish_empty_becomes_nonempty_rejected(self):
        calls = 0
        def add_at_finish(filters):
            nonlocal calls
            if "sales_order" in filters:
                calls += 1
                if calls == 2:
                    self.flow("LATE")
        self.list_hooks[self.module.FLOW] = add_at_finish
        with self.assertRaises(PermissionError):
            self.read()

    def test_candidate_company_docstatus_change_without_modified_rejected(self):
        for field, value in (("company", "OTHER"), ("docstatus", 2)):
            self.order[field] = "JF" if field == "company" else 1
            def change_after_query(filters):
                self.order[field] = value
            self.list_hooks["Sales Order"] = change_after_query
            with self.subTest(field=field), self.assertRaises((PermissionError, ValueError)):
                self.read()
            self.order.update(company="JF", docstatus=1)

    def test_candidate_unscoped_headers_and_incomplete_records_use_original_get_list(self):
        self.module.list_orders("JF", "2026-10-01", "2026-10-01", "SO")
        self.assertFalse(self.native_options)
        graph = self.module._Graph("JF")
        graph.records("Sales Order", {"name": "SO"}, {"name"}, complete=False)
        self.assertFalse(self.native_options)

    def test_candidate_fieldmask_and_authorization_change_still_rejected(self):
        self.hidden["Sales Order"] = {"company"}
        with self.assertRaises(PermissionError):
            self.read()
        self.hidden.clear()
        changed = False
        def revoke(fields):
            nonlocal changed
            if not changed:
                changed = True
                self.add("Has Role", "ADDED", parenttype="User", parent="native-reader", role="Sales User")
        self.list_hooks["Sales Order"] = revoke
        with self.assertRaises(PermissionError):
            self.read()


if __name__ == "__main__":
    # Only the new candidate cases, not the inherited existing model suite.
    suite = unittest.TestSuite()
    for cls in (PermissionPopulationModels, FixedEngineModels):
        for name in sorted(cls.__dict__):
            if name.startswith("test_candidate_"):
                suite.addTest(cls(name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
