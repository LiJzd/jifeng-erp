"""POS Settings read policy is limited to two existing business manager roles."""

import ast
from copy import deepcopy
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

from jifeng_erp.pos_settings_permissions import pos_settings_read_policy
from jifeng_erp.receipt_export_permissions import PERMISSION_FIELDS, same_permission_rules


class POSSettingsPermissionTests(unittest.TestCase):
    def setUp(self):
        self.fields = PERMISSION_FIELDS
        self.flags = set(self.fields) - {"role", "permlevel"}
        self.native = []
        for role in ("Sales User", "Accounts User", "System Manager"):
            row = {field: 0 for field in self.fields}
            row.update(role=role, permlevel=0, read=1, write=1, email=1, print=1, share=1)
            self.native.append(row)
        self.custom = []

    def expected(self):
        rows = deepcopy(self.native)
        for role in ("Sales Manager", "Accounts Manager"):
            row = {field: 0 for field in self.fields}
            row.update(role=role, permlevel=0, read=1)
            rows.append(row)
        return rows

    def test_exact_default_adds_only_read_and_preserves_every_native_rule(self):
        original = deepcopy(self.native)
        status, desired = pos_settings_read_policy(self.native, [], fields=self.fields)
        self.assertEqual(status, "install_default")
        self.assertEqual(self.native, original)
        self.assertTrue(same_permission_rules(desired, self.expected(), fields=self.fields))
        for row in desired[3:]:
            self.assertEqual((row["role"], row["permlevel"], row["read"]),
                             (row["role"], 0, 1))
            for flag in self.flags - {"read"}:
                self.assertEqual(row[flag], 0, flag)

    def test_existing_custom_policy_is_never_rewritten_and_exact_target_is_idempotent(self):
        self.assertEqual(pos_settings_read_policy(self.native, self.native, fields=self.fields)[0],
                         "site_policy_preserved")
        self.assertEqual(pos_settings_read_policy(self.native, self.expected(), fields=self.fields)[0],
                         "already_installed")
        changed = self.expected()
        changed[-1]["write"] = 1
        self.assertEqual(pos_settings_read_policy(self.native, changed, fields=self.fields)[0],
                         "site_policy_preserved")

    def test_changed_native_defaults_or_existing_manager_rule_are_not_overwritten(self):
        for change in ({"read": 0}, {"write": 0}, {"export": 1}, {"permlevel": 1}):
            native = deepcopy(self.native)
            native[0].update(change)
            self.assertEqual(pos_settings_read_policy(native, [], fields=self.fields)[0],
                             "native_rule_not_applicable")
        self.assertEqual(pos_settings_read_policy(
            self.native + [dict(self.native[0], role="Sales Manager")], [], fields=self.fields)[0],
            "native_rule_not_applicable")

    def test_setup_clones_rules_adds_two_read_rows_and_repeats_without_changes(self):
        frappe = types.ModuleType("frappe")
        frappe.get_meta = lambda name: types.SimpleNamespace(fields=[
            types.SimpleNamespace(fieldname=flag, fieldtype="Check") for flag in self.flags])

        def get_all(doctype, filters, fields, limit_page_length):
            self.assertEqual(filters, {"parent": "POS Settings"})
            self.assertEqual(limit_page_length, 0)
            source = self.native if doctype == "DocPerm" else self.custom
            return [{field: row.get(field, 0) for field in fields} for row in source]

        frappe.get_all = Mock(side_effect=get_all)

        def get_doc(payload):
            self.assertEqual((payload["doctype"], payload["parent"], payload["parenttype"],
                              payload["parentfield"]),
                             ("Custom DocPerm", "POS Settings", "DocType", "permissions"))

            def insert(ignore_permissions=False):
                self.assertTrue(ignore_permissions)
                self.custom.append({field: payload.get(field, 0) for field in self.fields})

            return types.SimpleNamespace(insert=insert)

        frappe.get_doc = Mock(side_effect=get_doc)
        frappe.throw = lambda message: (_ for _ in ()).throw(ValueError(message))
        permissions = types.ModuleType("frappe.permissions")

        def clone(doctype):
            self.assertEqual(doctype, "POS Settings")
            self.custom.extend(deepcopy(self.native))

        self.clone = permissions.setup_custom_perms = Mock(side_effect=clone)
        doctype_module = types.ModuleType("frappe.core.doctype.doctype.doctype")
        doctype_module.validate_permissions_for_doctype = Mock()
        with patch.dict(sys.modules, {"frappe": frappe, "frappe.permissions": permissions,
                                      "frappe.core.doctype.doctype.doctype": doctype_module}):
            tree = ast.parse((Path(__file__).parents[1] / "jifeng_erp" / "setup.py").read_text(encoding="utf-8"))
            helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                          and node.name == "_install_pos_settings_read_permission")
            namespace = {"frappe": frappe}
            exec(compile(ast.Module(body=[helper], type_ignores=[]), "pos-permission-helper-only", "exec"), namespace)
            install = namespace[helper.name]

            self.assertEqual(install()["status"], "installed")
            self.assertTrue(same_permission_rules(self.custom, self.expected(), fields=self.fields))
            cloned = deepcopy(self.custom)
            self.assertEqual(install()["status"], "already_installed")
            self.assertEqual(self.custom, cloned)
            self.clone.assert_called_once_with("POS Settings")
            doctype_module.validate_permissions_for_doctype.assert_called_once_with("POS Settings")


if __name__ == "__main__":
    unittest.main()
