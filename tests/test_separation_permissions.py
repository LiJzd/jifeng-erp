"""Default installation preserves the complete site's native permission policy."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

from jifeng_erp.separation_permissions import PERMISSION_FIELDS, default_separation_permissions


class TestSeparationPermissions(unittest.TestCase):
    def setUp(self):
        self.native = [dict(role="System Manager", permlevel=0, read=1, write=1, create=1,
                            submit=1, cancel=1, delete=1),
                       dict(role="HR Manager", permlevel=0, read=1, write=1, create=1, report=1),
                       dict(role="HR User", permlevel=0, read=1, write=1, create=1)]

    def test_exact_native_clone_and_paired_extension_are_recognized_independent_of_row_order(self):
        self.assertTrue(default_separation_permissions(self.native, list(reversed(self.native))))
        installed = [self.native[0], {**self.native[1], "submit": 1, "cancel": 1}, self.native[2]]
        self.assertTrue(default_separation_permissions(self.native, installed))

    def test_partial_grants_ownership_levels_and_other_rights_remain_site_policy(self):
        for changed in ({"submit": 1}, {"cancel": 1}, {"delete": 1}, {"if_owner": 1}, {"permlevel": 1},
                        {"read": 0}, {"write": 0}, {"export": 1}, {"mask": 1}):
            with self.subTest(changed=changed):
                self.assertFalse(default_separation_permissions(self.native,
                    [self.native[0], {**self.native[1], **changed}, self.native[2]]))

    def test_clerk_extra_duplicate_and_missing_rules_are_not_native_policy(self):
        for custom in ([self.native[0], self.native[1], {**self.native[2], "submit": 1, "cancel": 1}],
                       [*self.native, self.native[1]], self.native[:-1],
                       [*self.native, {"role": "Employee Self Service", "select": 1}],
                       [*self.native, {"role": "Custom HR", "read": 1}]):
            self.assertFalse(default_separation_permissions(self.native, custom))

    def test_future_native_check_fields_are_compared_and_native_changed_rights_win(self):
        fields = (*PERMISSION_FIELDS, "impersonate")
        custom = [self.native[0], {**self.native[1], "impersonate": 1}, self.native[2]]
        self.assertFalse(default_separation_permissions(self.native, custom, fields=fields))
        self.assertFalse(default_separation_permissions(
            [{**self.native[0], "delete": 0}, *self.native[1:]], self.native))


class Rule(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__

    def save(self, **kwargs):
        self.saves.append(kwargs)


class TestSeparationInstaller(unittest.TestCase):
    def setUp(self):
        self.native = [Rule(name="SYS", role="System Manager", permlevel=0, read=1, write=1, create=1,
                            submit=1, cancel=1, delete=1, impersonate=0),
                       Rule(name="MGR", role="HR Manager", permlevel=0, if_owner=0, read=1, write=1,
                            create=1, report=1, submit=0, cancel=0, delete=0, impersonate=0),
                       Rule(name="CLERK", role="HR User", permlevel=0, read=1, write=1, create=1,
                            submit=0, cancel=0, delete=0, impersonate=0)]
        self.custom, self.comments, self.saved = [], [], []
        self.frappe = types.ModuleType("frappe")
        self.frappe.throw = lambda message: (_ for _ in ()).throw(ValueError(message))
        self.hooks = {doctype: ["jifeng_erp.personnel_separation." + extension] for doctype, extension in (
            ("Employee Separation", "EmployeeSeparationMixin"), ("Employee", "SeparationEmployeeMixin"),
            ("Project", "SeparationProjectMixin"))}
        self.frappe.get_hooks = Mock(side_effect=lambda name: self.hooks)
        self.frappe.get_meta = Mock(return_value=types.SimpleNamespace(fields=[
            Rule(fieldname=field, fieldtype="Check") for field in (*PERMISSION_FIELDS, "impersonate")
            if field not in ("role", "permlevel")]))

        def get_all(doctype, filters=None, pluck=None, **kwargs):
            rows = self.native if doctype == "DocPerm" else self.custom
            rows = [row for row in rows if all(row.get(field, 0) == value for field, value in (filters or {}).items()
                                             if field != "parent")]
            return [row[pluck] for row in rows] if pluck else [dict(row) for row in rows]

        def get_doc(doctype, name):
            if doctype == "DocType":
                return types.SimpleNamespace(add_comment=lambda kind, message: self.comments.append((kind, message)))
            return next(row for row in self.custom if row.name == name)

        self.frappe.get_all = Mock(side_effect=get_all)
        self.frappe.get_doc = Mock(side_effect=get_doc)
        self.permissions = types.ModuleType("frappe.permissions")

        def copy_native(doctype):
            if not self.custom:
                self.custom = [Rule(deepcopy(row), saves=self.saved) for row in self.native]

        self.permissions.setup_custom_perms = Mock(side_effect=copy_native)
        self.doctype_module = types.ModuleType("frappe.core.doctype.doctype.doctype")
        self.doctype_module.validate_permissions_for_doctype = Mock()
        self.custom_field_module = types.ModuleType("frappe.custom.doctype.custom_field.custom_field")
        self.custom_field_module.create_custom_fields = Mock()
        modules = {"frappe": self.frappe, "frappe.permissions": self.permissions,
            "frappe.core.doctype.doctype.doctype": self.doctype_module,
            "frappe.custom.doctype.custom_field.custom_field": self.custom_field_module}
        self.patcher = patch.dict(sys.modules, modules)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        path = Path(__file__).parents[1] / "jifeng_erp" / "setup.py"
        spec = importlib.util.spec_from_file_location("separation_setup_under_test", path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_default_native_clone_changes_only_manager_pair_and_adds_one_audit_comment(self):
        self.module._install_separation_permissions()
        manager = next(row for row in self.custom if row.role == "HR Manager")
        self.assertEqual((manager.submit, manager.cancel, manager.delete), (1, 1, 0))
        for before, after in zip(self.native, self.custom):
            expected = dict(before)
            if before.role == "HR Manager":
                expected.update(submit=1, cancel=1)
            self.assertEqual({key: value for key, value in after.items() if key != "saves"}, expected)
        self.assertEqual(self.saved, [{"ignore_permissions": True}])
        self.assertEqual(len(self.comments), 1)
        self.assertEqual(self.comments[0][0], "Info")
        self.doctype_module.validate_permissions_for_doctype.assert_called_once_with("Employee Separation")
        self.module._install_separation_permissions()
        self.assertEqual(len(self.comments), 1)
        self.assertEqual(len(self.saved), 1)

    def test_semantic_clone_with_different_ids_and_order_is_preserved(self):
        self.custom = [Rule(deepcopy(row), name="CUSTOM-" + row.name, saves=self.saved)
                       for row in reversed(self.native)]
        original = [row.name for row in self.custom]
        self.module._install_separation_permissions()
        self.assertEqual([row.name for row in self.custom], original)
        self.assertEqual(len(self.saved), 1)

    def test_site_partial_or_future_permission_change_is_not_silently_overwritten(self):
        for change in ({"submit": 1}, {"if_owner": 1}, {"read": 0}, {"impersonate": 1}):
            self.custom = [Rule(deepcopy(row), saves=self.saved) for row in self.native]
            self.custom[1].update(change)
            baseline = deepcopy(self.custom)
            with self.subTest(change=change):
                self.module._install_separation_permissions()
                self.assertEqual(self.custom, baseline)
        self.permissions.setup_custom_perms.assert_not_called()
        self.doctype_module.validate_permissions_for_doctype.assert_not_called()
        self.assertEqual(self.comments, [])

    def test_unsafe_missing_controller_prevents_default_cancel_grant(self):
        self.hooks.pop("Project")
        with self.assertRaisesRegex(ValueError, "安全员工"):
            self.module._install_separation_permissions()
        self.permissions.setup_custom_perms.assert_not_called()
        self.assertEqual(self.custom, [])


if __name__ == "__main__":
    unittest.main()
