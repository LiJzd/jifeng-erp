"""Real candidate guard models for retained non-complete native read exclusion."""
import importlib
from copy import deepcopy
import test_ledger_field_mask_candidate as fixtures


# Inherit only the fixture setup, not the 30 existing tests a second time.
import unittest


class ReadableParentExclusion(unittest.TestCase):
    setUp = fixtures.CandidateModels.setUp
    receipt = fixtures.CandidateModels.receipt

    def test_normal_receipt_native_denied_is_wholly_excluded_even_with_child_mask(self):
        self.env.denied.add(("Purchase Receipt", "PR", "read"))
        self.env.meta["Purchase Receipt Item"].fields["amount"]["mask"] = 1
        columns, rows = self.proc.purchase_receipt_ledger(self.filters)
        self.assertEqual(rows, [])
        self.assertIn("amount", [column["fieldname"] for column in columns])
        self.assertNotIn("Purchase Receipt", [dt for dt, *_args in self.env.field_calls])
        self.assertGreaterEqual(self.env.permission_calls["Purchase Receipt", "PR", "read"], 1)

    def test_mixed_receipt_excludes_denied_and_preserves_allowed_full_projection(self):
        self.env.doc("Purchase Receipt", "ALLOW", **{key: deepcopy(value) for key, value in
            self.env.data["Purchase Receipt", "PR"].items_dict() if key not in ("doctype", "name")})
        self.env.denied.add(("Purchase Receipt", "PR", "read"))
        rows = self.proc.purchase_receipt_ledger(self.filters)[1]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["purchase_receipt"], rows[0]["amount"], rows[0]["accepted_qty"],
                          rows[0]["rejected_qty"]), ("ALLOW", self.pr_row.amount, 6, 3))
        self.assertIn("Purchase Receipt Item", [dt for dt, *_args in self.env.field_calls])

    def test_inventory_complete_still_rejects_native_denied_or_list_hidden_source(self):
        scope = importlib.import_module("jifeng_erp.inventory_scope")
        for collection in (self.env.denied, self.env.hidden):
            identity = ("Purchase Receipt", "PR", "read") if collection is self.env.denied else ("Purchase Receipt", "PR")
            with self.subTest(native_doc_denied=collection is self.env.denied):
                collection.add(identity)
                guard = self.guard.LedgerSourceGuard("purchase_receipt")
                with self.assertRaises(PermissionError):
                    scope.readable_documents("Purchase Receipt", {"name": "PR"}, complete=True, guard=guard)
                collection.remove(identity)

    def test_subcontract_normal_receipt_native_denied_returns_empty_report(self):
        self.env.denied.add(("Subcontracting Receipt", "SCR", "read"))
        self.env.meta["Subcontracting Receipt Item"].fields["process_loss_qty"]["mask"] = 1
        self.assertEqual(self.sub.subcontracting_receipt_ledger(self.filters)[1], [])
        self.assertNotIn("Subcontracting Receipt", [dt for dt, *_args in self.env.field_calls])

    def test_subcontract_complete_source_order_or_closure_still_rejects(self):
        self.env.denied.add(("Subcontracting Order", "SCO", "read"))
        with self.assertRaises(PermissionError):
            self.sub.subcontracting_receipt_ledger(self.filters)
        self.env.denied.clear()
        self.env.denied.add(("Subcontracting Receipt", "SCR", "read"))
        with self.assertRaises(PermissionError):
            self.sub.subcontracting_progress(self.filters)

    def test_unselected_warehouse_native_denied_is_excluded_and_not_registered(self):
        self.env.denied.add(("Warehouse", "MAIN", "read"))
        scope = importlib.import_module("jifeng_erp.inventory_scope")
        guard = self.guard.LedgerSourceGuard("purchase_receipt")
        result = scope.selected_warehouses(fixtures.Row(company="CO"), guard=guard)
        self.assertEqual(result, ["REJECT"])
        self.assertNotIn(("Warehouse", "MAIN"), guard.documents)
        self.assertIn(("Warehouse", "REJECT"), guard.documents)
        guard.finish()

    def test_explicit_selected_warehouse_native_denied_still_refuses_report(self):
        self.env.denied.add(("Warehouse", "MAIN", "read"))
        with self.assertRaises(PermissionError):
            self.proc.purchase_receipt_ledger({**self.filters, "warehouse": "MAIN"})

    def test_explicit_company_select_denied_still_refuses_report(self):
        self.env.denied.add(("Company", "CO", "select"))
        with self.assertRaises(PermissionError):
            self.proc.purchase_receipt_ledger(self.filters)


if __name__ == "__main__": unittest.main()
