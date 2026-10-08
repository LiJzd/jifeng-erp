"""Flow-first locking and current-source conflicts without a business database."""

import ast
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


class Record(dict):
    def __getattr__(self, key):
        return self[key]


class ValidationError(ValueError):
    pass


class TimestampMismatchError(ValidationError):
    pass


class NativePermissionError(Exception):
    pass


class NativeStockEntry:
    def __init__(self, test, header=None, rows=(), previous=None, new=False):
        self.test = test
        self.name = "SE-CURRENT"
        self.data = {"jf_material_issue_flow": header,
                     "items": [Record(jf_flow_order=flow) for flow in rows]}
        self.previous = previous
        self.new = new
        self.native_error = None
        self.native_mutation = None

    def get(self, field):
        return self.data.get(field)

    def is_new(self):
        return self.new

    def check_if_latest(self):
        # Model native load_doc_before_save as an independent current document,
        # not a reload of the preliminary snapshot. Native locks parent and
        # children here, then retains timestamp and transition permission gates.
        self.test.events.append(("native_stock_lock",))
        self._doc_before_save = self.previous
        if self.native_error:
            raise self.native_error
        if self.native_mutation:
            self.native_mutation(self)
        return "native-result"


class TestMaterialIssueLocking(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.snapshot_flows = []
        self.missing_flows = set()
        frappe = types.ModuleType("frappe")
        frappe.ValidationError = ValidationError
        frappe.TimestampMismatchError = TimestampMismatchError

        def throw(message, exception):
            raise exception(message)

        frappe.throw = throw
        frappe.db = types.SimpleNamespace(sql=self.sql, get_value=self.get_value)
        path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "material_issue_locking.py"
        spec = importlib.util.spec_from_file_location("material_issue_locking_under_test", path)
        self.locking = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"frappe": frappe}):
            spec.loader.exec_module(self.locking)
        self.controller = type("StockEntry", (self.locking.MaterialIssueLockingMixin, NativeStockEntry), {})

    def sql(self, query, values, as_dict=False):
        self.assertNotIn("FOR UPDATE", query.upper())
        self.assertIn("parenttype = 'Stock Entry'", query)
        self.assertIn("parentfield = 'items'", query)
        self.assertEqual(values, ("SE-CURRENT", "SE-CURRENT"))
        self.assertTrue(as_dict)
        self.events.append(("internal_snapshot",))
        return [Record(flow=flow) for flow in self.snapshot_flows]

    def get_value(self, doctype, name, field, for_update=False):
        if doctype == "Stock Entry":
            self.assertEqual(field, "jf_material_return_against")
            return None
        self.assertEqual((doctype, field), ("Jifeng Flow Order", "name"))
        self.assertTrue(for_update)
        self.events.append(("flow_lock", name))
        return None if name in self.missing_flows else name

    def previous(self, header=None, rows=()):
        return Record(jf_material_issue_flow=header,
                      items=[Record(jf_flow_order=flow) for flow in rows])

    def entry(self, **kwargs):
        return self.controller(self, **kwargs)

    def test_new_insert_locks_sorted_unique_current_sources_before_native(self):
        entry = self.entry(header="FLOW-B", rows=("FLOW-C", "FLOW-A", "FLOW-B", None, ""), new=True)
        self.assertEqual(entry.check_if_latest(), "native-result")
        self.assertEqual(self.events, [
            ("flow_lock", "FLOW-A"), ("flow_lock", "FLOW-B"),
            ("flow_lock", "FLOW-C"), ("native_stock_lock",),
        ])

    def test_existing_manual_row_edit_locks_old_and_new_sources(self):
        self.snapshot_flows = ["FLOW-A", "FLOW-B", "FLOW-A", None]
        entry = self.entry(rows=("FLOW-C",), previous=self.previous(rows=("FLOW-A", "FLOW-B")))
        self.assertEqual(entry.check_if_latest(), "native-result")
        self.assertEqual(self.events, [
            ("internal_snapshot",), ("flow_lock", "FLOW-A"),
            ("flow_lock", "FLOW-B"), ("flow_lock", "FLOW-C"), ("native_stock_lock",),
        ])

    def test_existing_tagged_issue_keeps_header_and_native_result(self):
        self.snapshot_flows = ["FLOW-A", "FLOW-A"]
        entry = self.entry(header="FLOW-A", rows=("FLOW-A",),
                           previous=self.previous(header="FLOW-A", rows=("FLOW-A",)))
        self.assertEqual(entry.check_if_latest(), "native-result")
        self.assertEqual(self.events.count(("native_stock_lock",)), 1)

    def test_existing_header_cannot_be_removed_replaced_or_newly_bound(self):
        for old, current in (("FLOW-A", None), ("FLOW-A", "FLOW-B"), (None, "FLOW-B")):
            with self.subTest(old=old, current=current):
                self.events = []
                self.snapshot_flows = [old]
                entry = self.entry(header=current, previous=self.previous(header=old))
                with self.assertRaisesRegex(ValidationError, "来源不可更改"):
                    entry.check_if_latest()
                self.assertEqual(self.events[-1], ("native_stock_lock",))

    def test_empty_stored_header_normalizes_null_without_new_binding(self):
        self.snapshot_flows = [""]
        entry = self.entry(header=None, previous=self.previous(header=""))
        self.assertEqual(entry.check_if_latest(), "native-result")
        self.assertEqual(self.events, [("internal_snapshot",), ("native_stock_lock",)])

    def test_old_snapshot_missing_actual_row_source_conflicts_without_late_lock(self):
        self.snapshot_flows = ["FLOW-A"]
        entry = self.entry(rows=("FLOW-A",),
                           previous=self.previous(rows=("FLOW-A", "HIDDEN-FLOW-NEW")))
        with self.assertRaises(TimestampMismatchError) as caught:
            entry.check_if_latest()
        self.assertNotIn("HIDDEN-FLOW-NEW", str(caught.exception))
        self.assertEqual(self.events, [
            ("internal_snapshot",), ("flow_lock", "FLOW-A"), ("native_stock_lock",),
        ])

    def test_old_snapshot_missing_actual_header_conflicts_without_source_leak(self):
        entry = self.entry(previous=self.previous(header="HIDDEN-FLOW-NEW"))
        with self.assertRaises(TimestampMismatchError) as caught:
            entry.check_if_latest()
        self.assertNotIn("HIDDEN-FLOW-NEW", str(caught.exception))
        self.assertEqual(self.events, [("internal_snapshot",), ("native_stock_lock",)])

    def test_old_snapshot_extra_removed_sources_can_remain_locked(self):
        self.snapshot_flows = ["FLOW-A", "FLOW-REMOVED"]
        entry = self.entry(rows=("FLOW-A",), previous=self.previous(rows=("FLOW-A",)))
        self.assertEqual(entry.check_if_latest(), "native-result")
        self.assertEqual(self.events, [
            ("internal_snapshot",), ("flow_lock", "FLOW-A"),
            ("flow_lock", "FLOW-REMOVED"), ("native_stock_lock",),
        ])

    def test_native_timestamp_error_remains_native_and_runs_once(self):
        entry = self.entry(header="FLOW-A", new=True)
        original = TimestampMismatchError("native timestamp conflict")
        entry.native_error = original
        with self.assertRaises(TimestampMismatchError) as caught:
            entry.check_if_latest()
        self.assertIs(caught.exception, original)
        self.assertEqual(self.events, [("flow_lock", "FLOW-A"), ("native_stock_lock",)])

    def test_native_transition_permission_error_is_not_replaced(self):
        entry = self.entry(header="FLOW-A", new=True)
        original = NativePermissionError("native submit/cancel permission")
        entry.native_error = original
        with self.assertRaises(NativePermissionError) as caught:
            entry.check_if_latest()
        self.assertIs(caught.exception, original)
        self.assertEqual(self.events[-1], ("native_stock_lock",))

    def test_missing_flow_rejects_without_source_id_or_stock_lock(self):
        self.missing_flows = {"HIDDEN-MISSING-FLOW"}
        entry = self.entry(header="HIDDEN-MISSING-FLOW", new=True)
        with self.assertRaises(ValidationError) as caught:
            entry.check_if_latest()
        self.assertNotIn("HIDDEN-MISSING-FLOW", str(caught.exception))
        self.assertNotIn(("native_stock_lock",), self.events)

    def test_native_callback_cannot_introduce_an_unlocked_source(self):
        entry = self.entry(new=True)
        entry.native_mutation = lambda doc: doc.data.update(jf_material_issue_flow="NEW-FLOW")
        with self.assertRaises(TimestampMismatchError):
            entry.check_if_latest()
        self.assertEqual(self.events, [("native_stock_lock",)])

    def test_new_ordinary_stock_entry_keeps_native_path_without_source_queries(self):
        entry = self.entry(new=True)
        self.assertEqual(entry.check_if_latest(), "native-result")
        self.assertEqual(self.events, [("native_stock_lock",)])

    def test_return_locks_flow_and_source_issue_before_native_return(self):
        entry = self.entry(new=True)
        entry.data["jf_material_return_against"] = "MI-SOURCE"
        events = self.events
        with patch.object(self.locking, "_persisted_flow_ids", side_effect=lambda name: {"FLOW-A"} if name == "MI-SOURCE" else set()), \
             patch.object(self.locking, "_lock_flows", side_effect=lambda names: events.append(("flow_locks", tuple(sorted(names))))), \
             patch.object(self.locking, "_lock_issue_sources", side_effect=lambda names: events.append(("issue_locks", tuple(sorted(names))))):
            self.assertEqual(entry.check_if_latest(), "native-result")
        self.assertEqual(events, [
            ("flow_locks", ("FLOW-A",)), ("issue_locks", ("MI-SOURCE",)), ("native_stock_lock",),
        ])

    def test_cancelling_issue_checks_for_linked_return_after_native_lock(self):
        entry = self.entry()
        entry.data.update(docstatus=2, stock_entry_type="Material Issue")
        previous = self.previous()
        previous["docstatus"] = 1
        entry.previous = previous
        returns = types.ModuleType("jifeng_erp.material_returns")
        returns.assert_no_material_returns = lambda name: self.events.append(("return_guard", name))
        with patch.dict(sys.modules, {"jifeng_erp.material_returns": returns}), \
             patch.object(self.locking, "_persisted_flow_ids", return_value=set()), \
             patch.object(self.locking, "_persisted_return_source", return_value=None), \
             patch.object(self.locking, "_lock_flows"), patch.object(self.locking, "_lock_issue_sources"):
            self.assertEqual(entry.check_if_latest(), "native-result")
        self.assertEqual(self.events, [("native_stock_lock",), ("return_guard", "SE-CURRENT")])

    def test_hook_extends_native_stock_entry_with_the_locking_mixin(self):
        path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "hooks.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hook = next(node for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "extend_doctype_class"
                            for target in node.targets))
        extension = ast.literal_eval(hook.value)
        self.assertEqual(extension["Stock Entry"],
                         ["jifeng_erp.material_issue_locking.MaterialIssueLockingMixin"])


if __name__ == "__main__":
    unittest.main()
