"""Archive listing must retain native visibility and audit projection."""

from copy import deepcopy
import importlib.util
import sys
import unittest
from unittest.mock import patch

import test_inventory_catalog as model


class TestInventoryArchiveList(unittest.TestCase):
    def setUp(self):
        self.environment = model.TestInventoryCatalog()
        self.environment.setUp()
        self.addCleanup(self.environment.doCleanups)
        env = self.environment
        import jifeng_erp
        self.patch = patch.object(jifeng_erp, "inventory_catalog", env.module, create=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        spec = importlib.util.spec_from_file_location("inventory_archive_list_under_test", model.ROOT / "jifeng_erp/inventory_archive_list.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        env.permitted[model.PROFILE] |= self.module.AUDIT_FIELDS
        env.profile.update(owner="old-user@example.invalid", creation=env.profile.modified,
                           modified_by="new-user@example.invalid")
        original = env.matches
        env.matches = lambda document, filters: all(
            document.get(field) in value[1] if isinstance(value, list) and value[0] == "in"
            else original(document, {field: value}) for field, value in filters.items())

    def query(self, **args):
        return self.module.query_archives("物料", "JF", **args)

    def test_native_audit_and_readonly_projection_with_real_page_boundaries(self):
        env = self.environment
        env.second_archive()
        env.documents[model.PROFILE, "PROFILE-B"].inactive = 1
        before = deepcopy(env.documents)
        result = self.query(page_size=1)
        self.assertEqual((result["total"], result["total_pages"], result["page"], result["status"]), (2, 2, 1, "全部"))
        row = result["rows"][0]
        self.assertEqual(row["audit"]["owner"], "old-user@example.invalid")
        self.assertEqual(row["audit"]["modified"], row["profile_version"])
        self.assertEqual(row["profile"]["list_price"], "17.50")
        self.assertTrue(row["readonly"])
        self.assertFalse(row["can_write"])
        self.assertEqual(self.query(page=99, page_size=1)["page"], 2)
        self.assertEqual(self.query(status="在用")["total"], 1)
        self.assertEqual(env.documents, before)
        for method in (env.frappe.db.commit, env.frappe.db.sql, env.frappe.db.set_value, env.frappe.new_doc, env.frappe.delete_doc):
            method.assert_not_called()

    def test_audit_omission_and_restrictions_do_not_recover_private_columns(self):
        env = self.environment
        env.hidden[model.PROFILE] = {"owner"}
        env.projection_omissions[model.PROFILE] = {"creation"}
        row = self.query()["rows"][0]
        self.assertNotIn("owner", row["audit"])
        self.assertNotIn("creation", row["audit"])
        env.doc_denials.add(("Item", "ITEM-A", "read"))
        self.assertEqual(self.query()["total"], 0)

    def test_audit_changes_and_late_revocation_fail_closed(self):
        env = self.environment
        calls = 0
        def change_after_audit():
            nonlocal calls
            calls += 1
            if calls == 2:
                env.profile.owner = "changed-native-user"
        env.list_hooks[model.PROFILE] = change_after_audit
        with self.assertRaises(PermissionError):
            self.query()
        env.list_hooks.clear()
        loads = 0
        def revoke_after_load(name):
            nonlocal loads
            loads += 1
            if loads >= 3:
                env.hidden[model.PROFILE] = {"owner"}
        env.doc_hooks[model.PROFILE] = revoke_after_load
        with self.assertRaises(PermissionError):
            self.query()

    def test_empty_invalid_scope_and_query_caps(self):
        self.assertEqual(self.query(search="no-such-archive", page=2)["page"], 1)
        self.assertEqual(self.query(search="no-such-archive")["rows"], [])
        for page in [True, "01", "1.5", 0, 1001]:
            with self.assertRaises(ValueError):
                self.query(page=page)
        self.environment.doc_denials.add(("Company", "JF", "select"))
        with self.assertRaises(PermissionError):
            self.query()


if __name__ == "__main__":
    unittest.main()
