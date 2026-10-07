"""Committed stock wakes the native scheduler; no native/Redis execution."""

import ast
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]
BASE = "erpnext.stock.doctype.repost_item_valuation.repost_item_valuation."


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class CommitCallbacks:
    def __init__(self):
        self.callbacks = []

    def add(self, callback):
        self.callbacks.append(callback)

    def commit(self):
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            callback()

    def rollback(self):
        self.callbacks = []


class TestFlowReposting(unittest.TestCase):
    def setUp(self):
        self.callbacks = CommitCallbacks()
        self.frappe = types.ModuleType("frappe")
        self.frappe.conf, self.frappe.session = Record(), Record(user="ordinary-stock-actor")
        self.frappe.db = Record(after_commit=self.callbacks, get_single_value=Mock(return_value=0),
            get_value=Mock(return_value="NATIVE-SCHEDULE"), commit=Mock(), sql=Mock(), set_value=Mock())
        self.job = Record(method=BASE + "repost_entries", stopped=0, server_script=None)
        self.native_job_pending, self.native_enqueues = False, 0
        def native_enqueue(force=False):
            self.assertTrue(force)
            # Simulate the independently supplied native SJT queue contract,
            # including an already queued/running task's fixed job ID.
            if self.native_job_pending:
                return False
            self.native_job_pending = True
            self.native_enqueues += 1
            return True
        self.job.enqueue = Mock(side_effect=native_enqueue)
        self.frappe.get_doc = Mock(return_value=self.job)
        self.frappe.log_error, self.frappe.enqueue, self.frappe.set_user = Mock(), Mock(), Mock()
        scheduler = types.ModuleType("frappe.utils.scheduler")
        self.disabled = scheduler.is_scheduler_disabled = Mock(return_value=False)
        modules = patch.dict(sys.modules, {"frappe": self.frappe,
            "frappe.utils": types.ModuleType("frappe.utils"), "frappe.utils.scheduler": scheduler})
        modules.start()
        self.addCleanup(modules.stop)
        spec = importlib.util.spec_from_file_location("flow_reposting_under_test", ROOT / "jifeng_erp/flow_reposting.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def doc(self, doctype="Stock Entry", *, status=1, linked=True, update_stock=True):
        return Record(doctype=doctype, docstatus=status, update_stock=update_stock,
            items=[Record(jf_flow_order="FLOW" if linked else None)])

    def test_four_native_stock_types_schedule_submit_and_cancel_only_after_commit(self):
        for doctype in ("Stock Entry", "Subcontracting Receipt", "Delivery Note", "Sales Invoice"):
            for event, state in (("on_submit", 1), ("on_cancel", 2)):
                with self.subTest(doctype=doctype, event=event):
                    self.module.schedule_flow_stock_reposting(self.doc(doctype, status=state), event)
        self.assertEqual(len(self.callbacks.callbacks), 8)
        self.frappe.get_doc.assert_not_called()
        self.job.enqueue.assert_not_called()
        self.callbacks.commit()
        self.assertEqual(self.native_enqueues, 1)
        self.assertEqual(self.job.enqueue.call_count, 8)
        self.frappe.get_doc.assert_called_with("Scheduled Job Type", "NATIVE-SCHEDULE")

    def test_rollback_drops_callback_and_later_transaction_can_schedule(self):
        self.module.schedule_flow_stock_reposting(self.doc(), "on_submit")
        self.callbacks.rollback()
        self.callbacks.commit()
        self.job.enqueue.assert_not_called()
        self.module.schedule_flow_stock_reposting(self.doc(status=2), "on_cancel")
        self.callbacks.commit()
        self.assertEqual(self.native_enqueues, 1)

    def test_unlinked_pure_invoice_draft_and_other_events_never_schedule(self):
        cases = [(self.doc(linked=False), "on_submit"), (self.doc("Sales Invoice", update_stock=False), "on_submit"),
            (self.doc("Sales Invoice", status=2, update_stock=False), "on_cancel"),
            (self.doc("Purchase Order"), "on_submit"), (self.doc(status=0), "on_submit"),
            (self.doc(), "on_cancel"), (self.doc(), "validate")]
        for doc, event in cases:
            self.module.schedule_flow_stock_reposting(doc, event)
        self.callbacks.commit()
        self.assertEqual(self.callbacks.callbacks, [])
        self.frappe.db.get_value.assert_not_called()

    def test_actual_material_issue_header_is_an_eligible_flow_link(self):
        doc = self.doc(linked=False)
        doc["jf_material_issue_flow"] = "FLOW"
        self.module.schedule_flow_stock_reposting(doc, "on_submit")
        self.callbacks.commit()
        self.assertEqual(self.native_enqueues, 1)

    def test_parallel_setting_chooses_existing_normal_job_and_does_not_change_actor_or_queue(self):
        self.frappe.db.get_single_value.return_value = 1
        self.job.method = BASE + "run_parallel_reposting"
        self.module.schedule_flow_stock_reposting(self.doc(), "on_submit")
        self.callbacks.commit()
        self.frappe.db.get_value.assert_called_once_with("Scheduled Job Type", {"method": BASE + "run_parallel_reposting"}, "name")
        self.job.enqueue.assert_called_once_with(force=True)
        self.frappe.set_user.assert_not_called()
        self.frappe.enqueue.assert_not_called()
        self.assertEqual(self.frappe.session.user, "ordinary-stock-actor")

    def test_disabled_scheduler_maintenance_and_stopped_job_are_respected(self):
        for setting in ("disabled", "maintenance", "stopped"):
            self.disabled.return_value = setting == "disabled"
            self.frappe.conf["maintenance_mode"] = setting == "maintenance"
            self.job.stopped = setting == "stopped"
            self.module.schedule_flow_stock_reposting(self.doc(), "on_submit")
            self.callbacks.commit()
        self.job.enqueue.assert_not_called()
        self.frappe.log_error.assert_not_called()

    def test_scheduling_failure_keeps_committed_stock_success_and_safe_error_record(self):
        self.job.enqueue.side_effect = ConnectionError("private connection details")
        self.module.schedule_flow_stock_reposting(self.doc(), "on_submit")
        self.callbacks.commit()
        self.frappe.log_error.assert_called_once()
        message = self.frappe.log_error.call_args.kwargs["message"]
        self.assertIn("ConnectionError", message)
        self.assertIn("勿重复提交", message)
        self.assertNotIn("private connection details", message)
        self.frappe.db.commit.assert_not_called()
        self.frappe.db.sql.assert_not_called()
        self.frappe.db.set_value.assert_not_called()
        self.frappe.log_error.side_effect = RuntimeError("log unavailable")
        self.module.schedule_flow_stock_reposting(self.doc(), "on_submit")
        self.callbacks.commit()  # An unavailable log must not report stock failure either.

    def test_missing_or_reconfigured_job_is_logged_without_running_another_method(self):
        for change in ("missing", "method", "script"):
            self.frappe.db.get_value.return_value = None if change == "missing" else "NATIVE-SCHEDULE"
            self.job.method = BASE + "repost_entries" if change != "method" else "other.method"
            self.job.server_script = "OTHER" if change == "script" else None
            self.module.schedule_flow_stock_reposting(self.doc(), "on_submit")
            self.callbacks.commit()
        self.assertEqual(self.frappe.log_error.call_count, 3)
        self.job.enqueue.assert_not_called()

    def test_hooks_keep_existing_validators_and_attach_all_four_stock_events(self):
        tree = ast.parse((ROOT / "jifeng_erp/hooks.py").read_text(encoding="utf-8"))
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "doc_events" for target in node.targets))
        events = ast.literal_eval(assignment.value)
        hook = "jifeng_erp.flow_reposting.schedule_flow_stock_reposting"
        for doctype in ("Stock Entry", "Subcontracting Receipt", "Delivery Note", "Sales Invoice"):
            self.assertEqual((events[doctype]["on_submit"], events[doctype]["on_cancel"]), (hook, hook))
        self.assertEqual(events["Stock Entry"]["validate"], "jifeng_erp.setup.validate_flow_links")
        self.assertEqual(events["Delivery Note"]["before_validate"], "jifeng_erp.fulfillment.validate_fulfillment_flow")


if __name__ == "__main__":
    unittest.main()
