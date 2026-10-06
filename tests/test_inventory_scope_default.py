"""Existing no-guard callers retain Company/native Doc/warehouse semantics."""
import unittest
import fixture_adapter
from fixture_adapter import Row


class InventoryScopeDefaultTests(unittest.TestCase):
    def setUp(self):
        self.hidden, self.full_denied = set(), set()
        self.documents = {"Purchase Receipt": {"PR": Row(name="PR", company="CO", items=[])},
            "Warehouse": {"GROUP": Row(name="GROUP", company="CO", is_group=1, lft=0, rgt=9),
                          "OLD": Row(name="OLD", company="CO", is_group=0, lft=1, rgt=2, disabled=1),
                          "NEW": Row(name="NEW", company="CO", is_group=0, lft=3, rgt=4)}}
        fixture_adapter.install(self, lambda: self.documents, "CO", "SUP", ())

    def assert_no_fields(self):
        self.assertEqual((self.env.field_calls, self.env.mask_calls), ([], []))

    def test_report_scope_default_retains_company_select_without_profile_read(self):
        filters = Row(company="CO", from_date="2026-09-01", to_date="2026-09-30")
        self.assertEqual(self.scope.report_scope(filters, ("Purchase Receipt",)),
                         (filters, filters.from_date, filters.to_date))
        self.assertEqual(self.env.permission_calls, {})
        self.assert_no_fields()

    def test_partial_documents_still_exclude_native_read_denied_parent(self):
        self.full_denied.add(("Purchase Receipt", "PR"))
        self.assertEqual(self.scope.readable_documents("Purchase Receipt", {"company": "CO"}), {})
        self.assertIn(("Purchase Receipt", "PR", "read"), self.env.permission_calls)
        self.assert_no_fields()

    def test_complete_documents_still_reject_hidden_or_native_denied_source(self):
        for denied in (self.hidden, self.full_denied):
            with self.subTest(hidden=denied is self.hidden):
                denied.add(("Purchase Receipt", "PR"))
                with self.assertRaises(PermissionError):
                    self.scope.readable_documents("Purchase Receipt", {"company": "CO"}, complete=True)
                denied.clear()
        self.assert_no_fields()

    def test_group_warehouses_keep_disabled_history_and_do_not_infer_hidden_child(self):
        filters = Row(company="CO", warehouse="GROUP")
        self.assertEqual(self.scope.selected_warehouses(filters), ["OLD", "NEW"])
        self.hidden.add(("Warehouse", "NEW"))
        self.assertEqual(self.scope.selected_warehouses(filters), ["OLD"])
        self.assert_no_fields()


if __name__ == "__main__": unittest.main()
