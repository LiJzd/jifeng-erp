"""Default HRMS permission clones may be extended; changed site policies win."""

import unittest

from jifeng_erp.wage_permissions import default_wage_permissions


class TestWagePermissions(unittest.TestCase):
    def setUp(self):
        self.native = [dict(role="HR Manager", permlevel=0, read=1, write=1, create=1, submit=1,
                            cancel=0, delete=0, report=1),
                       dict(role="HR User", permlevel=0, read=1, write=1, create=1, submit=1)]
        self.ess = dict(role="Employee Self Service", permlevel=0, select=1, export=1)

    def test_exact_native_clone_and_verified_default_ess(self):
        self.assertTrue(default_wage_permissions(self.native, self.native))
        self.assertTrue(default_wage_permissions(self.native, [*self.native, self.ess], ess_generated=True))

    def test_default_ess_requires_source_proof(self):
        self.assertFalse(default_wage_permissions(self.native, [*self.native, self.ess]))

    def test_site_denial_and_additional_right_remain_untouched(self):
        for changed in ({"read": 0}, {"if_owner": 1}, {"mask": 1}, {"import": 1}, {"cancel": 1}):
            with self.subTest(changed=changed):
                custom = [{**self.native[0], **changed}, self.native[1]]
                self.assertFalse(default_wage_permissions(self.native, custom))

    def test_custom_ess_read_or_export_policy_is_not_default(self):
        for changed in ({"read": 1}, {"export": 0}, {"permlevel": 1}):
            with self.subTest(changed=changed):
                self.assertFalse(default_wage_permissions(self.native,
                    [*self.native, {**self.ess, **changed}], ess_generated=True))

    def test_extra_or_duplicate_roles_are_not_default(self):
        self.assertFalse(default_wage_permissions(self.native, [*self.native, {"role": "Custom HR"}]))
        self.assertFalse(default_wage_permissions(self.native, [*self.native, self.native[0]]))
        self.assertFalse(default_wage_permissions(self.native, [*self.native, self.ess, self.ess], ess_generated=True))

    def test_installed_pair_is_idempotent_and_does_not_change_clerk(self):
        custom = [{**self.native[0], "cancel": 1, "delete": 1}, self.native[1], self.ess]
        self.assertTrue(default_wage_permissions(self.native, custom, ess_generated=True))
        custom[1] = {**custom[1], "cancel": 1, "delete": 1}
        self.assertFalse(default_wage_permissions(self.native, custom, ess_generated=True))

    def test_changed_native_reversal_policy_is_respected(self):
        native = [{**self.native[0], "cancel": 1, "delete": 1}, self.native[1]]
        self.assertFalse(default_wage_permissions(native, self.native))

    def test_future_permission_fields_are_compared(self):
        fields = ("role", "permlevel", "new_restricted_right")
        self.assertFalse(default_wage_permissions(self.native,
            [{**self.native[0], "new_restricted_right": 1}, self.native[1]], fields=fields))


if __name__ == "__main__":
    unittest.main()
