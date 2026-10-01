"""Guard native fixture source checks and worker-free repost protocol writes."""

import ast
from copy import deepcopy
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, call, patch


ROOT = Path(__file__).parents[1]
CALLBACKS = ("before_commit", "after_commit", "before_rollback", "after_rollback")


class Flags(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Queue:
    def __init__(self, functions):
        self._functions = list(functions)

    def add(self, function):
        self._functions.append(function)

    def reset(self):
        self._functions.clear()


def functions(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), namespace)


class TestNativeNestedGateCleanup(unittest.TestCase):
    """Run the real nested gate/audits with native-style callback invalidation."""

    def setUp(self):
        self.rows, self.events, self.checks = [], [], []
        self.cache_failure = None
        self.original_callbacks = {name: [Mock(name=name + "-first"), Mock(name=name + "-second")] for name in CALLBACKS}
        self.frappe = types.SimpleNamespace(db=types.SimpleNamespace(**{
            name: Queue(functions) for name, functions in self.original_callbacks.items()}),
            session=types.SimpleNamespace(user="reader"), in_test=True, PermissionError=PermissionError,
            flags=Flags(currently_saving=[("Original", "SOURCE")]),
            local=types.SimpleNamespace(_realtime_log=[{"event": "original"}]),
            generate_hash=lambda length: "native-test-point")
        self.frappe.set_user = lambda user: (self.events.append(("actor", user)), setattr(self.frappe.session, "user", user))
        self.frappe.clear_cache = lambda **kwargs: self.invalidate("user", kwargs["user"])
        self.frappe.clear_document_cache = lambda *args: self.invalidate("document", args)
        callbacks = types.ModuleType("jifeng_erp.personnel_changes_validation")
        callbacks.frappe = self.frappe
        functions("jifeng_erp/personnel_changes_validation.py", {"_callbacks", "_restore_callbacks"}, callbacks.__dict__)
        context = types.ModuleType("jifeng_erp.personnel_separation_validation")
        context.frappe, context.deepcopy = self.frappe, deepcopy
        functions("jifeng_erp/personnel_separation_validation.py", {"_local_context", "_restore_local_context"}, context.__dict__)
        modules = patch.dict(sys.modules, {callbacks.__name__: callbacks, context.__name__: context})
        modules.start()
        self.addCleanup(modules.stop)
        self.callbacks, self.context = callbacks, context
        namespace = {"frappe": self.frappe, "_callbacks": callbacks._callbacks, "_local_context": context._local_context,
            "_end_rollback_only": self.rollback, "_begin_rollback_only": Mock(), "_contents": lambda: deepcopy(self.rows),
            "actors": {"reader": "reader"}, "records": {("Sales Order", "SO"), ("Item", "FG")},
            "company": "QA", "order": types.SimpleNamespace(name="SO"), "read_order": lambda company, name: {"order": name},
            "passed": self.checks.append}
        functions("jifeng_erp/catalog_validation.py", {"_profile_rollback", "_profile_restore_caller", "_profile_audit"}, namespace)
        source = ROOT / "jifeng_erp/sales_fulfillment_validation.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        smoke = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "smoke")
        read = deepcopy(next(node for node in smoke.body if isinstance(node, ast.FunctionDef) and node.name == "read"))
        gate = deepcopy(next(node for node in smoke.body if isinstance(node, ast.FunctionDef) and node.name == "nested_gate"))
        # Retain the real `nonlocal current` and cleanup audit assertions.
        wrapper = ast.FunctionDef(name="make_gate", args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[],
            kw_defaults=[], defaults=[]), body=[ast.Assign(targets=[ast.Name(id="current", ctx=ast.Store())],
            value=ast.Constant(value=None)), gate, ast.Return(value=ast.Name(id="nested_gate", ctx=ast.Load()))], decorator_list=[])
        module = ast.fix_missing_locations(ast.Module(body=[read, wrapper], type_ignores=[]))
        exec(compile(module, str(source), "exec"), namespace)
        self.gate = namespace["make_gate"]()
        self.context_before = context._local_context()

    def invalidate(self, kind, identity):
        self.events.append(("invalidate", kind, identity))
        # Real clear_document_cache registers transaction invalidation jobs.
        # They appear AFTER savepoint rollback restored the original queues.
        self.frappe.db.after_commit.add(Mock(name="new-cache-commit"))
        self.frappe.db.after_rollback.add(Mock(name="new-cache-rollback"))
        self.frappe.local._realtime_log = [{"event": "cache-invalidated"}]
        self.frappe.flags.currently_saving = [("Cache", "new")]
        if self.cache_failure and self.cache_failure[0] == kind:
            raise self.cache_failure[1]

    def rollback(self, point):
        self.events.append(("rollback", point))
        self.rows.clear()

    def setup(self):
        self.rows.append({"owned": "nested restriction"})
        self.frappe.db.before_rollback.add(Mock(name="nested-before-rollback"))
        self.frappe.db.after_rollback.add(Mock(name="nested-after-rollback"))
        self.frappe.local._realtime_log = [{"event": "nested setup"}]
        self.frappe.flags.currently_saving = [("Nested", "new")]

    def assert_restored(self):
        self.assertEqual(self.rows, [])
        self.assertEqual(self.context._local_context(), self.context_before)
        self.assertEqual(self.frappe.session.user, "reader")
        actual = self.callbacks._callbacks()
        for name, original in self.original_callbacks.items():
            self.assertEqual(actual[name], original)
            for restored, caller in zip(actual[name], original, strict=True):
                self.assertIs(restored, caller)
                caller.assert_not_called()

    def test_cache_invalidations_after_rollback_restore_exact_original_callback_identity_order_and_context(self):
        self.gate("nested-source-gate", self.setup, errors=None)
        self.assertEqual(self.checks, ["nested-source-gate"])
        self.assert_restored()
        rollback = next(index for index, event in enumerate(self.events) if event[0] == "rollback")
        invalidations = [index for index, event in enumerate(self.events) if event[0] == "invalidate"]
        self.assertEqual(len(invalidations), 3)
        self.assertTrue(all(index > rollback for index in invalidations))
        self.assertEqual(self.events[invalidations[0]], ("invalidate", "user", "reader"))

    def test_user_or_document_cache_failure_still_restores_queues_and_context_and_propagates_error(self):
        for kind in ("user", "document"):
            with self.subTest(kind=kind):
                failure = RuntimeError(kind + " native cache clear failed")
                self.cache_failure = (kind, failure)
                with self.assertRaises(RuntimeError) as raised:
                    self.gate("failed-cache-gate", self.setup, errors=None)
                self.assertIs(raised.exception, failure)
                self.assert_restored()
                self.assertEqual(self.checks, [])

    def test_primary_business_exception_remains_original_when_cleanup_cache_invalidation_also_fails(self):
        primary, cleanup = ValueError("native business setup failed"), RuntimeError("native cache cleanup failed")
        self.cache_failure = ("document", cleanup)

        def failing_setup():
            self.setup()
            raise primary

        with self.assertRaises(ValueError) as raised:
            self.gate("primary-error-gate", failing_setup, errors=None)
        self.assertIs(raised.exception, primary)
        self.assertTrue(any("native cache cleanup failed" in note for note in primary.__notes__))
        self.assert_restored()
        self.assertEqual(self.checks, [])


class TestSalesRepostProtocolFixture(unittest.TestCase):
    def setUp(self):
        self.frappe = types.SimpleNamespace(db=types.SimpleNamespace(set_value=Mock()),
                                            enqueue=Mock(), get_doc=Mock())
        self.job = types.SimpleNamespace(doctype="Repost Payment Ledger", name="OWNED-RPL", docstatus=0,
            repost_vouchers=[types.SimpleNamespace(doctype="Repost Payment Ledger Items", name="OWNED-RPL-ROW")],
            submit=Mock(), save=Mock(), start_repost=Mock())
        self.insert = Mock(return_value=self.job)
        self.voucher = types.SimpleNamespace(doctype="Payment Entry", name="OWNED-PE", company="QA",
                                              posting_date="2026-10-01", docstatus=0)
        path = ROOT / "jifeng_erp/sales_fulfillment_validation.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_repost_protocol_state"]
        namespace = {"frappe": self.frappe}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
        self.persist = namespace["_repost_protocol_state"]

    def assert_no_worker_or_voucher_write(self):
        self.frappe.enqueue.assert_not_called()
        self.frappe.get_doc.assert_not_called()
        for action in (self.job.submit, self.job.save, self.job.start_repost):
            action.assert_not_called()
        for invocation in self.frappe.db.set_value.call_args_list:
            self.assertIn(invocation.args[0], {"Repost Payment Ledger", "Repost Payment Ledger Items"})
            self.assertNotIn(invocation.args[1], {"OWNED-PE", "OWNED-JE"})

    def test_exact_normal_draft_insert_and_only_own_protocol_fields_for_every_status(self):
        for doctype in ("Payment Entry", "Journal Entry"):
            self.voucher.doctype = doctype
            for status in ("Queued", "Failed", "Completed"):
                with self.subTest(doctype=doctype, status=status):
                    self.insert.reset_mock()
                    self.frappe.db.set_value.reset_mock()
                    self.assertIs(self.persist(self.insert, self.voucher, status), self.job)
                    self.insert.assert_called_once_with("Repost Payment Ledger", company="QA", posting_date="2026-10-01",
                        add_manually=1, repost_vouchers=[{"voucher_type": doctype, "voucher_no": "OWNED-PE"}])
                    self.assertEqual(self.frappe.db.set_value.call_args_list, [
                        call("Repost Payment Ledger", "OWNED-RPL", {"docstatus": 1, "repost_status": status}),
                        call("Repost Payment Ledger Items", "OWNED-RPL-ROW", "docstatus", 1),
                    ])
                    self.assertEqual(self.voucher.docstatus, 0)
                    self.assert_no_worker_or_voucher_write()

    def test_unknown_status_fails_before_any_write(self):
        with self.assertRaises(AssertionError):
            self.persist(self.insert, self.voucher, "In Progress")
        self.insert.assert_not_called()
        self.frappe.db.set_value.assert_not_called()

    def test_unknown_or_unpersisted_voucher_fails_before_any_write(self):
        for doctype, name in (("Sales Invoice", "SI"), ("Payment Entry", None)):
            self.voucher.doctype, self.voucher.name = doctype, name
            with self.subTest(doctype=doctype), self.assertRaises(AssertionError):
                self.persist(self.insert, self.voucher, "Queued")
        self.insert.assert_not_called()
        self.frappe.db.set_value.assert_not_called()

    def test_unexpected_non_draft_job_never_mutates_protocol_or_runs_worker(self):
        self.job.docstatus = 1
        with self.assertRaises(AssertionError):
            self.persist(self.insert, self.voucher, "Queued")
        self.frappe.db.set_value.assert_not_called()
        self.assert_no_worker_or_voucher_write()

    def test_normal_insert_failure_propagates_without_fallback_write_or_submit(self):
        self.insert.side_effect = PermissionError("native insert denied")
        with self.assertRaises(PermissionError):
            self.persist(self.insert, self.voucher, "Queued")
        self.insert.assert_called_once()
        self.frappe.db.set_value.assert_not_called()
        self.assert_no_worker_or_voucher_write()


class NativeRow(dict):
    def __getattribute__(self, field):
        if dict.__contains__(self, field):
            return dict.__getitem__(self, field)
        return dict.__getattribute__(self, field)


class TestNativeManufacturingSourceGate(unittest.TestCase):
    """A native verifier must fail on omitted batches, raw rows or crossed SO lines."""

    def setUp(self):
        path = ROOT / "jifeng_erp/sales_fulfillment_validation.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "_assert_native_manufacturing_sources"]
        namespace = {"flt": lambda value: float(value or 0)}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
        self.verify = namespace["_assert_native_manufacturing_sources"]
        self.order = NativeRow(name="SO", company="QA", items=[
            NativeRow(name="SO-ROW-A", item_code="SAME-FG", qty=6),
            NativeRow(name="SO-ROW-B", item_code="SAME-FG", qty=4)])
        self.sources, self.result = [], {"work_orders": []}
        for index, detail in enumerate(self.order.items):
            count = 2 if index == 0 else 1
            flow = NativeRow(name="FLOW-" + str(index), company="QA", sales_order="SO",
                sales_order_item=detail.name, item_code="SAME-FG", work_order="WO-" + str(index), planned_qty=detail.qty)
            work = NativeRow(name="WO-" + str(index), company="QA", sales_order="SO", sales_order_item=detail.name,
                production_item="SAME-FG", jf_flow_order=flow.name, docstatus=1, operations=[],
                status="In Process", stock_uom="Nos", qty=detail.qty, produced_qty=count, process_loss_qty=0)
            entries, batches = [], []
            for batch in range(count):
                name = "STE-" + str(index) + "-" + str(batch)
                finished = NativeRow(name=name + "-FG", parent=name, parenttype="Stock Entry", parentfield="items",
                    item_code="SAME-FG", is_finished_item=1, jf_flow_order=flow.name, uom="Nos", stock_uom="Nos",
                    qty=1, transfer_qty=1)
                raw = NativeRow(name=name + "-RAW", item_code="RAW", is_finished_item=0,
                                jf_flow_order=flow.name, transfer_qty=1)
                entry = NativeRow(name=name, company="QA", work_order=work.name, purpose="Manufacture",
                                  docstatus=1, fg_completed_qty=1, items=[raw, finished])
                entries.append(entry)
                batches.append({"document": {field: entry[field] for field in (
                    "name", "company", "work_order", "purpose", "docstatus", "fg_completed_qty")},
                    "finished_items": [dict(finished)]})
            self.sources.append((flow, work, entries))
            self.result["work_orders"].append({"document": {key: value for key, value in work.items()
                                                            if key != "operations"}, "manufacture_entries": batches})

    def verify_fixture(self):
        self.verify(self.result, self.order, "RAW", self.sources)

    def test_same_item_two_order_lines_keep_two_work_orders_and_all_three_native_batches(self):
        self.verify_fixture()

    def test_omitted_second_batch_is_rejected_even_if_work_order_produced_quantity_is_correct(self):
        self.result["work_orders"][0]["manufacture_entries"].pop()
        with self.assertRaises(AssertionError):
            self.verify_fixture()

    def test_same_item_does_not_allow_crossing_exact_sales_order_detail_or_flow(self):
        for field, wrong in (("sales_order_item", "SO-ROW-B"), ("jf_flow_order", "FLOW-1")):
            with self.subTest(field=field):
                result = deepcopy(self.result)
                result["work_orders"][0]["document"][field] = wrong
                with self.assertRaises(AssertionError):
                    self.verify(result, self.order, "RAW", self.sources)

    def test_native_flow_reverse_binding_or_other_so_detail_cannot_be_adopted_as_expectation(self):
        for field, wrong in (("work_order", "WO-1"), ("sales_order_item", "SO-ROW-B")):
            with self.subTest(field=field):
                sources = list(self.sources)
                flow, work, entries = sources[0]
                sources[0] = (NativeRow(dict(flow)), work, entries)
                sources[0][0][field] = wrong
                with self.assertRaises(AssertionError):
                    self.verify(self.result, self.order, "RAW", sources)

    def test_raw_material_cannot_replace_finished_row_despite_same_quantity(self):
        entry = self.sources[0][2][0]
        self.result["work_orders"][0]["manufacture_entries"][0]["finished_items"] = [dict(entry.items[0])]
        with self.assertRaises(AssertionError):
            self.verify_fixture()

    def test_same_item_finished_row_from_other_batch_is_rejected(self):
        batch = self.result["work_orders"][0]["manufacture_entries"][0]
        batch["finished_items"] = deepcopy(self.result["work_orders"][0]["manufacture_entries"][1]["finished_items"])
        with self.assertRaises(AssertionError):
            self.verify_fixture()

    def test_planned_work_order_quantity_cannot_replace_native_batch_quantity(self):
        batch = self.result["work_orders"][0]["manufacture_entries"][0]
        batch["finished_items"][0]["qty"] = 6
        with self.assertRaises(AssertionError):
            self.verify_fixture()

    def test_foreign_work_order_or_duplicate_native_batch_is_rejected(self):
        for field, wrong in (("company", "OTHER"), ("work_order", "WO-1")):
            with self.subTest(field=field):
                result = deepcopy(self.result)
                result["work_orders"][0]["manufacture_entries"][0]["document"][field] = wrong
                with self.assertRaises(AssertionError):
                    self.verify(result, self.order, "RAW", self.sources)
        self.result["work_orders"][0]["manufacture_entries"][1] = deepcopy(
            self.result["work_orders"][0]["manufacture_entries"][0])
        with self.assertRaises(AssertionError):
            self.verify_fixture()


if __name__ == "__main__":
    unittest.main()
