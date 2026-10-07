"""The native map's default policy is narrow and site decisions are retained."""

import ast
from copy import deepcopy
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

from jifeng_erp.receipt_export_permissions import PERMISSION_FIELDS, same_permission_rules


class SubcontractingBomPermissionTests(unittest.TestCase):
    def setUp(self):
        self.flags = (set(PERMISSION_FIELDS) - {"role", "permlevel"}) | {"impersonate"}
        self.fields = ("role", "permlevel", *sorted(self.flags))
        self.native = [dict(role="System Manager", permlevel=0, read=1, create=1, write=1,
            delete=1, report=1, export=1, share=1, print=1, email=1)]
        self.custom = []
        self.frappe = types.ModuleType("frappe")
        self.frappe.get_meta = lambda name: types.SimpleNamespace(istable=0, is_submittable=0,
            fields=[types.SimpleNamespace(fieldname=flag, fieldtype="Check") for flag in self.flags])
        def rows(doctype, filters, fields, limit_page_length):
            self.assertEqual(filters, {"parent": "Subcontracting BOM"})
            self.assertEqual(limit_page_length, 0)
            return deepcopy(self.native if doctype == "DocPerm" else self.custom)
        self.frappe.get_all = Mock(side_effect=rows)
        def document(payload):
            self.assertEqual((payload["doctype"], payload["parent"], payload["parenttype"], payload["parentfield"]),
                ("Custom DocPerm", "Subcontracting BOM", "DocType", "permissions"))
            def insert(ignore_permissions=False):
                self.assertTrue(ignore_permissions)
                self.custom.append(deepcopy(payload))
            return types.SimpleNamespace(insert=insert)
        self.frappe.get_doc = Mock(side_effect=document)
        self.frappe.throw = lambda message: (_ for _ in ()).throw(ValueError(message))
        self.frappe.db = types.SimpleNamespace(commit=Mock(), set_value=Mock(), sql=Mock())
        permissions = types.ModuleType("frappe.permissions")
        self.clone = permissions.setup_custom_perms = Mock(side_effect=lambda name: self.custom.extend(deepcopy(self.native)))
        doctype = types.ModuleType("frappe.core.doctype.doctype.doctype")
        self.validate = doctype.validate_permissions_for_doctype = Mock()
        modules = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.permissions": permissions,
            "frappe.core.doctype.doctype.doctype": doctype})
        modules.start()
        self.addCleanup(modules.stop)
        tree = ast.parse((Path(__file__).parents[1] / "jifeng_erp/setup.py").read_text(encoding="utf-8"))
        helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == "_install_subcontracting_bom_permission")
        namespace = {"frappe": self.frappe}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), "permission-helper-only", "exec"), namespace)
        self.install = namespace[helper.name]

    def test_default_keeps_system_manager_and_grants_only_three_mapping_actions(self):
        before = deepcopy(self.native)
        result = self.install()
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["permissions"], ["read", "create", "write"])
        self.assertEqual(self.native, before)
        self.assertTrue(same_permission_rules(self.custom[:1], before, fields=self.fields))
        addition = self.custom[1]
        self.assertEqual((addition["role"], addition["permlevel"]), ("Manufacturing Manager", 0))
        for flag in self.flags:
            self.assertEqual(addition[flag], int(flag in {"read", "create", "write"}), flag)
        self.clone.assert_called_once_with("Subcontracting BOM")
        self.validate.assert_called_once_with("Subcontracting BOM")
        self.frappe.db.commit.assert_not_called()
        self.frappe.db.sql.assert_not_called()
        self.frappe.db.set_value.assert_not_called()

    def test_existing_site_policy_including_plain_clone_is_never_overridden(self):
        for existing in (deepcopy(self.native), [dict(role="Manufacturing Manager", permlevel=0, read=0)],
                         [dict(role="Another Role", permlevel=0, read=1)]):
            self.custom = existing
            before = deepcopy(existing)
            self.assertEqual(self.install()["status"], "site_policy_preserved")
            self.assertEqual(self.custom, before)
        self.clone.assert_not_called()
        self.frappe.get_doc.assert_not_called()

    def test_repeat_install_and_existing_native_manager_rule_are_noops_even_explicit_denial(self):
        self.install()
        before = deepcopy(self.custom)
        self.assertEqual(self.install()["status"], "site_policy_preserved")
        self.assertEqual(self.custom, before)
        self.custom = []
        for read in (0, 1):
            self.native = [self.native[0], dict(role="Manufacturing Manager", permlevel=0, read=read)]
            self.assertEqual(self.install()["status"], "native_role_preserved")
        self.assertEqual(self.frappe.get_doc.call_count, 1)

    def test_changed_default_or_extra_native_role_is_not_treated_as_a_fresh_site(self):
        default = deepcopy(self.native)
        for change in (dict(read=0), dict(if_owner=1), dict(delete=0), dict(permlevel=1)):
            self.native = deepcopy(default)
            self.native[0].update(change)
            self.assertEqual(self.install()["status"], "native_rule_not_applicable")
        self.native = default + [dict(role="Another Role", read=1)]
        self.assertEqual(self.install()["status"], "native_rule_not_applicable")
        self.clone.assert_not_called()
        self.frappe.get_doc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
