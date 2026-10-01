"""Persisted repost read-gate fixtures must never run a worker or touch ledgers."""

import ast
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, call


ROOT = Path(__file__).parents[1]


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


if __name__ == "__main__":
    unittest.main()
