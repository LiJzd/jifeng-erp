import copy
import unittest

from jifeng_erp.receipt_export_permissions import (
    PERMISSION_FIELDS, same_permission_rules, stock_receipt_export_policy,
)


class ReceiptExportPermissionTests(unittest.TestCase):
    def setUp(self):
        self.native = [
            dict(role="Stock Manager", permlevel=0, read=1, write=1, submit=1, report=1),
            dict(role="Stock Manager", permlevel=1, read=1, write=1),
            dict(role="Stock User", permlevel=0, read=1),
            dict(role="Purchase User", permlevel=0, read=1, if_owner=1),
        ]

    def test_fresh_policy_changes_only_unconditional_supervisor_export(self):
        original = copy.deepcopy(self.native)
        status, desired = stock_receipt_export_policy(self.native, [])
        self.assertEqual(status, "install_default")
        self.assertEqual(self.native, original)
        self.assertEqual([row["export"] for row in desired], [1, 0, 0, 0])
        desired[0]["export"] = 0
        self.assertTrue(same_permission_rules(desired, self.native))

    def test_existing_native_clone_is_an_explicit_site_decision(self):
        status, _ = stock_receipt_export_policy(self.native, copy.deepcopy(self.native))
        self.assertEqual(status, "site_policy_preserved")

    def test_repeat_install_and_native_enable_are_noops(self):
        _, desired = stock_receipt_export_policy(self.native, [])
        self.assertEqual(stock_receipt_export_policy(self.native, desired)[0], "already_installed")
        native_enabled = copy.deepcopy(self.native)
        native_enabled[0]["export"] = 1
        self.assertEqual(stock_receipt_export_policy(native_enabled, [])[0], "native_already_enabled")

    def test_every_other_flag_and_role_change_is_site_policy(self):
        _, desired = stock_receipt_export_policy(self.native, [])
        for field in PERMISSION_FIELDS:
            if field in ("role", "export"):
                continue
            with self.subTest(field=field):
                custom = copy.deepcopy(desired)
                custom[0][field] = 1 - custom[0][field]
                self.assertEqual(stock_receipt_export_policy(self.native, custom)[0], "site_policy_preserved")
        for custom in (desired[:-1], desired + desired[:1], desired + [dict(role="Another Role", read=1)]):
            self.assertEqual(stock_receipt_export_policy(self.native, custom)[0], "site_policy_preserved")

    def test_duplicate_owned_or_unreadable_supervisor_rules_never_grant(self):
        for target in (dict(if_owner=1), dict(read=0)):
            native = copy.deepcopy(self.native)
            native[0].update(target)
            self.assertEqual(stock_receipt_export_policy(native, [])[0], "native_rule_not_applicable")
        self.assertEqual(stock_receipt_export_policy(self.native + self.native[:1], [])[0], "native_rule_not_applicable")
        self.assertEqual(stock_receipt_export_policy(self.native[1:], [])[0], "native_rule_not_applicable")

    def test_new_native_flag_is_preserved_and_checked(self):
        fields = (*PERMISSION_FIELDS, "future_permission")
        native = copy.deepcopy(self.native)
        native[0]["future_permission"] = 1
        _, desired = stock_receipt_export_policy(native, [], fields=fields)
        self.assertEqual(desired[0]["future_permission"], 1)
        desired[0]["future_permission"] = 0
        self.assertEqual(stock_receipt_export_policy(native, desired, fields=fields)[0], "site_policy_preserved")

    def test_malformed_permission_values_fail_before_any_plan(self):
        for value in (-1, 2, 1.5, "true"):
            with self.subTest(value=value):
                native = copy.deepcopy(self.native)
                native[0]["read"] = value
                with self.assertRaises(ValueError):
                    stock_receipt_export_policy(native, [])


if __name__ == "__main__":
    unittest.main()
