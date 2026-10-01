"""Default wage roles get source reads; intentional native site rules win."""

import ast
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class TestWageSourcePermissions(unittest.TestCase):
    def setUp(self):
        self.rules, self.created, self.comments = {}, [], []
        self.frappe = types.ModuleType("frappe")
        self.frappe.get_meta = lambda doctype: types.SimpleNamespace(permissions=self.rules.setdefault(doctype, []))
        self.frappe.get_doc = self.document
        native = types.ModuleType("frappe.core.doctype.doctype.doctype")
        native.validate_permissions_for_doctype = Mock()
        permissions = types.ModuleType("frappe.permissions")
        permissions.setup_custom_perms = Mock()
        self.modules = {"frappe": self.frappe, native.__name__: native, permissions.__name__: permissions}
        tree = ast.parse((Path(__file__).parents[1] / "jifeng_erp/setup.py").read_text(encoding="utf-8"))
        definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                          and node.name == "_install_wage_source_permissions")
        namespace = {"frappe": self.frappe}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), "source_permission_installer", "exec"), namespace)
        self.install = namespace[definition.name]

    def document(self, values, name=None):
        if values == "DocType":
            return types.SimpleNamespace(add_comment=lambda kind, text: self.comments.append((name, kind, text)))
        def insert(**kwargs):
            self.created.append(values)
            self.rules[values["parent"]].append(types.SimpleNamespace(**values))
        return types.SimpleNamespace(insert=insert)

    def run_install(self):
        with patch.dict(sys.modules, self.modules):
            self.install()

    def test_both_normal_wage_roles_receive_no_production_writes(self):
        self.run_install()
        self.assertEqual(len(self.created), 12)
        for permission in self.created:
            self.assertIn(permission["role"], ("HR User", "HR Manager"))
            self.assertEqual(permission["select"], 1)
            self.assertEqual(permission["read"], int(permission["parent"] not in ("Operation", "UOM")))
            self.assertFalse(any(permission.get(field) for field in (
                "write", "create", "submit", "cancel", "delete", "amend", "export", "share", "print")))
        self.assertEqual(len(self.comments), 12)
        self.run_install()
        self.assertEqual(len(self.created), 12, "migration must not duplicate grants or audit notes")

    def test_explicit_clerk_denial_and_manager_restriction_are_kept(self):
        original = [types.SimpleNamespace(role="HR User", read=0, select=0),
                    types.SimpleNamespace(role="HR Manager", read=1, select=1, if_owner=1)]
        self.rules["Job Card"] = list(original)
        self.run_install()
        self.assertEqual(self.rules["Job Card"], original)
        self.assertFalse(any(row["parent"] == "Job Card" for row in self.created))

    def test_shared_native_selection_does_not_imply_reading_full_sources(self):
        self.rules["UOM"] = [types.SimpleNamespace(role="All", read=0, select=1)]
        self.rules["Job Card"] = [types.SimpleNamespace(role="All", read=0, select=1)]
        self.run_install()
        self.assertFalse(any(row["parent"] == "UOM" for row in self.created))
        self.assertEqual(len([row for row in self.created if row["parent"] == "Job Card"]), 2)


if __name__ == "__main__":
    unittest.main()
