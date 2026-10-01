"""Native catalog rejection cleanup must not delete files before SQL rollback."""

import ast
from copy import deepcopy
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]
CALLBACKS = ("before_commit", "after_commit", "before_rollback", "after_rollback")


class Flags(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Queue:
    def __init__(self, function):
        self._functions = [function]

    def add(self, function):
        self._functions.append(function)

    def reset(self):
        self._functions.clear()


def functions(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), namespace)


class TestCatalogNativeRejection(unittest.TestCase):
    def setUp(self):
        self.rows, self.events, self.rollback_fails, self.blob = [], [], False, False
        self.old = {key: Mock() for key in CALLBACKS}
        self.frappe = types.SimpleNamespace(flags=Flags(currently_saving=[("Old", "DOC")]),
            local=types.SimpleNamespace(_realtime_log=[{"event": "old"}]))
        self.frappe.db = types.SimpleNamespace(savepoint=Mock(), rollback=Mock(side_effect=self.rollback),
            **{key: Queue(function) for key, function in self.old.items()})
        callbacks = types.ModuleType("jifeng_erp.personnel_changes_validation")
        callbacks.frappe = self.frappe
        functions("jifeng_erp/personnel_changes_validation.py", {"_callbacks", "_restore_callbacks"}, callbacks.__dict__)
        context = types.ModuleType("jifeng_erp.personnel_separation_validation")
        context.frappe, context.deepcopy = self.frappe, deepcopy
        functions("jifeng_erp/personnel_separation_validation.py", {"_local_context", "_restore_local_context"}, context.__dict__)
        scope = patch.dict(sys.modules, {"jifeng_erp.personnel_changes_validation": callbacks,
                                       "jifeng_erp.personnel_separation_validation": context})
        scope.start()
        self.addCleanup(scope.stop)
        self.callbacks, self.context = callbacks, context
        namespace = {"frappe": self.frappe, "_catalog_contents": lambda: deepcopy(self.rows),
                     "_ledger_snapshot": lambda: {"GL/SLE": "unchanged"}}
        functions("jifeng_erp/catalog_validation.py", {"_catalog_rejected"}, namespace)
        self.reject = namespace["_catalog_rejected"]
        self.queues_before, self.context_before = callbacks._callbacks(), context._local_context()

    def rollback(self, **kwargs):
        self.events.append("database")
        if self.rollback_fails:
            raise RuntimeError("database rollback failed")
        self.rows.clear()

    def action(self, error=PermissionError("denied")):
        self.rows.append({"File": "new fixture", "Version": "new fixture"})
        self.blob = True
        self.frappe.local._realtime_log = [{"event": "new"}]
        self.frappe.flags.currently_saving = [("New", "DOC")]
        self.frappe.db.before_rollback.add(lambda: self.events.append("before"))
        self.frappe.db.after_rollback.add(self.file_cleanup)
        self.frappe.db.after_commit.add(Mock())
        if error is not None:
            raise error

    def file_cleanup(self):
        self.assertFalse(self.rows, "File aliases must disappear before on_rollback")
        self.events.append("file")
        self.blob = False

    def assert_restored(self):
        self.assertEqual(self.callbacks._callbacks(), self.queues_before)
        self.assertEqual(self.context._local_context(), self.context_before)
        for function in self.old.values():
            function.assert_not_called()

    def test_expected_native_rejection_runs_file_cleanup_after_sql_and_restores_all_context(self):
        self.reject(self.action, PermissionError, "POINT")
        self.assertEqual(self.events, ["before", "database", "file"])
        self.assertFalse(self.blob)
        self.assert_restored()

    def test_database_rollback_failure_never_runs_native_file_deletion(self):
        self.rollback_fails = True
        with self.assertRaisesRegex(AssertionError, "database_rollback"):
            self.reject(self.action, PermissionError, "POINT")
        self.assertEqual(self.events, ["before", "database"])
        self.assertTrue(self.blob)
        self.assert_restored()

    def test_unexpected_error_is_preserved_after_full_cleanup(self):
        original = RuntimeError("native unexpected")
        with self.assertRaises(RuntimeError) as result:
            self.reject(lambda: self.action(original), PermissionError, "POINT")
        self.assertIs(result.exception, original)
        self.assertFalse(self.blob)
        self.assert_restored()

    def test_unexpectedly_successful_operation_cannot_pass_but_is_still_cleaned(self):
        with self.assertRaisesRegex(AssertionError, "应拒绝"):
            self.reject(lambda: self.action(None), PermissionError, "POINT")
        self.assertFalse(self.blob)
        self.assert_restored()

    def test_original_unexpected_error_keeps_cleanup_failure_note_without_replacing_it(self):
        self.rollback_fails = True
        original = RuntimeError("native original")
        with self.assertRaises(RuntimeError) as result:
            self.reject(lambda: self.action(original), PermissionError, "POINT")
        self.assertIs(result.exception, original)
        self.assertTrue(any("database_rollback" in note for note in original.__notes__))
        self.assertTrue(self.blob)
        self.assert_restored()


class TestExactReportLogCleanup(unittest.TestCase):
    def setUp(self):
        self.rows = {"OLD": Flags(name="OLD", owner="other", method="historic", error="historic")}
        self.before = deepcopy(self.rows)
        self.evidence = []
        self.frappe = types.SimpleNamespace(session=types.SimpleNamespace(user="qa-role"))
        self.frappe.set_user = lambda user: setattr(self.frappe.session, "user", user)
        self.frappe.delete_doc = Mock(side_effect=lambda doctype, name, **kwargs: self.rows.pop(name))
        hashing = types.ModuleType("jifeng_erp.product_dictionary_validation")
        hashing._sql_row_hash = lambda rows: repr(rows)
        scope = patch.dict(sys.modules, {"jifeng_erp.product_dictionary_validation": hashing})
        scope.start()
        self.addCleanup(scope.stop)
        namespace = {"frappe": self.frappe, "_error_log_rows": lambda: deepcopy(self.rows)}
        functions("jifeng_erp/catalog_validation.py", {"_clean_expected_report_error"}, namespace)
        self.clean = lambda: namespace["_clean_expected_report_error"](self.before, "REPORT", "qa-role", self.evidence)

    def add_expected(self, **changes):
        self.rows["NEW"] = Flags(name="NEW", owner="qa-role", method="Report execution failed for: REPORT",
                                 error="query_report.py: frappe.exceptions.PermissionError") | changes
        self.rows["NEW"] = Flags(self.rows["NEW"])

    def test_only_exact_current_report_log_is_deleted_and_caller_restored(self):
        self.add_expected()
        self.clean()
        self.assertEqual(self.rows, self.before)
        self.frappe.delete_doc.assert_called_once_with("Error Log", "NEW", delete_permanently=True)
        self.assertEqual(self.frappe.session.user, "qa-role")
        self.assertEqual(self.evidence[0]["name"], "NEW")

    def test_unrelated_attachment_owner_or_exception_never_deleted(self):
        for changes in ({"method": "Error Attaching File"}, {"owner": "other"}, {"error": "query_report.py: ValidationError"}):
            self.add_expected(**changes)
            with self.subTest(changes=changes), self.assertRaises(AssertionError):
                self.clean()
            self.frappe.delete_doc.assert_not_called()
            self.assertEqual(self.frappe.session.user, "qa-role")

    def test_changed_historical_log_or_multiple_new_logs_refuses_all_deletion(self):
        self.add_expected()
        self.rows["OLD"].error = "changed"
        with self.assertRaises(AssertionError):
            self.clean()
        self.frappe.delete_doc.assert_not_called()
        self.rows["OLD"] = deepcopy(self.before["OLD"])
        self.rows["ANOTHER"] = deepcopy(self.rows["NEW"])
        with self.assertRaises(AssertionError):
            self.clean()
        self.frappe.delete_doc.assert_not_called()

    def test_no_new_log_causes_no_write(self):
        self.clean()
        self.frappe.delete_doc.assert_not_called()
        self.assertEqual(self.evidence, [])

    def test_native_delete_failure_restores_caller_and_preserves_receipt(self):
        self.add_expected()
        self.frappe.delete_doc.side_effect = RuntimeError("delete failed")
        with self.assertRaises(RuntimeError):
            self.clean()
        self.assertEqual(self.frappe.session.user, "qa-role")
        self.assertEqual(self.evidence, [])
        self.assertIn("NEW", self.rows)


class TestSuccessfulNativeLogGuard(unittest.TestCase):
    def setUp(self):
        self.rows = {"OLD": Flags(name="OLD", error="historic")}
        self.frappe = types.SimpleNamespace(in_test=True)
        hashing = types.ModuleType("jifeng_erp.product_dictionary_validation")
        hashing._sql_row_hash = lambda rows: repr(rows)
        scope = patch.dict(sys.modules, {"jifeng_erp.product_dictionary_validation": hashing})
        scope.start()
        self.addCleanup(scope.stop)
        namespace = {"frappe": self.frappe, "_error_log_rows": lambda: deepcopy(self.rows)}
        functions("jifeng_erp/catalog_validation.py", {"_native_without_error_log"}, namespace)
        self.native = namespace["_native_without_error_log"]

    def test_success_uses_native_context_and_restores_it(self):
        def action():
            self.assertFalse(self.frappe.in_test)
            return "saved"
        self.assertEqual(self.native(action, "save"), "saved")
        self.assertTrue(self.frappe.in_test)

    def test_caught_attachment_error_stops_at_exact_action_without_deleting_log(self):
        def action():
            self.rows["NEW"] = Flags(name="NEW", error="attachment error")
        with self.assertRaisesRegex(AssertionError, "update:Jifeng Product Profile:inactive"):
            self.native(action, "update:Jifeng Product Profile:inactive")
        self.assertIn("NEW", self.rows)
        self.assertTrue(self.frappe.in_test)

    def test_historical_log_change_cannot_pass(self):
        with self.assertRaises(AssertionError):
            self.native(lambda: self.rows["OLD"].update(error="changed"), "save")
        self.assertTrue(self.frappe.in_test)

    def test_expected_exception_remains_original_and_context_is_restored(self):
        original = PermissionError("native denial")
        def action():
            raise original
        with self.assertRaises(PermissionError) as result:
            self.native(action, "denied")
        self.assertIs(result.exception, original)
        self.assertTrue(self.frappe.in_test)


if __name__ == "__main__":
    unittest.main()
