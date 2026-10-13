"""Public navigation upgrades must retain site choices and surviving row IDs."""
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1] / "jifeng_erp"


class Row(types.SimpleNamespace):
    pass


class Sidebar(types.SimpleNamespace):
    def remove(self, row):
        self.items.remove(row)

    def append(self, field, value):
        row = Row(**value, name="new-" + str(len(self.items)))
        getattr(self, field).append(row)
        return row

    def set(self, field, value):
        setattr(self, field, value)

    def save(self):
        self.saved += 1

    def add_comment(self, *args):
        self.comments.append(args)


def link(name, target, kind="DocType"):
    return Row(name=name, type="Link", label=name, link_type=kind, link_to=target, route_options="")


class TestNativeNavigation(unittest.TestCase):
    def setUp(self):
        self.fake = types.SimpleNamespace(db=types.SimpleNamespace(exists=lambda *args: False))
        with patch.dict(sys.modules, {"frappe": self.fake}):
            spec = importlib.util.spec_from_file_location("native_nav", ROOT / "desktop_entry.py")
            self.nav = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.nav)
        self.shortcuts = [Row(type="DocType", link_to="Purchase Order", label="采购订单")]

    def test_upgrade_preserves_other_links_and_child_identity_and_is_idempotent(self):
        home = link("home-id", self.nav.WORKSPACE, "Workspace")
        custom = link("site-id", "Customer")
        sidebar = Sidebar(items=[home, link("old-id", "jifeng-modern", "Page"), custom])
        self.assertTrue(self.nav._update_sidebar(sidebar, self.shortcuts))
        self.assertIs(sidebar.items[0], home)
        self.assertIs(sidebar.items[-1], custom)
        self.assertEqual([r.link_to for r in sidebar.items], [self.nav.WORKSPACE, "Purchase Order", "Customer"])
        self.assertEqual([r.idx for r in sidebar.items], [1, 2, 3])
        self.assertFalse(self.nav._update_sidebar(sidebar, self.shortcuts))

    def test_existing_business_links_move_into_fixture_order_with_ids_kept(self):
        purchase = link("purchase-id", "Purchase Order")
        purchase.idx = 20
        custom = link("site-id", "Customer")
        sidebar = Sidebar(items=[link("home-id", self.nav.WORKSPACE, "Workspace"), custom, purchase])
        self.assertTrue(self.nav._update_sidebar(sidebar, self.shortcuts))
        self.assertIs(sidebar.items[1], purchase)
        self.assertEqual(purchase.idx, 2)
        self.assertIs(sidebar.items[-1], custom)
        self.assertFalse(self.nav._update_sidebar(sidebar, self.shortcuts))

    def test_old_first_page_becomes_home_with_the_same_identity(self):
        home = link("same-id", "jifeng-legacy", "Page")
        sidebar = Sidebar(items=[home])
        self.nav._update_sidebar(sidebar, self.shortcuts)
        self.assertIs(sidebar.items[0], home)
        self.assertEqual((home.link_type, home.link_to), ("Workspace", self.nav.WORKSPACE))

    def test_existing_old_named_workspace_is_a_site_choice(self):
        self.fake.db.exists = lambda kind, name: name == "Jifeng ERP"
        home = link("same-id", "Jifeng ERP", "Workspace")
        self.nav._update_sidebar(Sidebar(items=[home]), [])
        self.assertEqual(home.link_to, "Jifeng ERP")

    def test_private_or_other_module_sidebar_is_not_saved(self):
        for fields in ({"for_user": "someone"}, {"app": "other_app"}, {"module": "Other"}):
            sidebar = Sidebar(items=[link("old", "jifeng-modern", "Page")],
                              for_user=None, app=None, module="Jifeng ERP", saved=0, comments=[])
            for key, value in fields.items():
                setattr(sidebar, key, value)
            self.fake.db.exists = lambda kind, name: name != "Jifeng ERP"
            self.fake.get_doc = lambda kind, name: Row(shortcuts=self.shortcuts) if kind == "Workspace" else sidebar
            self.assertFalse(self.nav.repair_legacy_desktop_entry())
            self.assertEqual(sidebar.saved, 0)
            self.assertEqual(sidebar.items[0].link_to, "jifeng-modern")

    def test_workspace_groups_keep_all_extension_doctypes(self):
        doc = json.loads((ROOT / "jifeng_erp/workspace/jifeng_erp/jifeng_erp.json").read_text(encoding="utf-8"))
        blocks = json.loads(doc["content"])
        self.assertEqual([b["data"]["text"] for b in blocks if b["type"] == "header"],
                         ["常用业务", "生产与计件", "聚水潭协作"])
        self.assertEqual(len(doc["shortcuts"]), 19)
        self.assertFalse(any(s["link_to"] in self.nav.LEGACY_PAGES for s in doc["shortcuts"]))
        self.assertEqual({b["data"]["shortcut_name"] for b in blocks if b["type"] == "shortcut"},
                         {s["label"] for s in doc["shortcuts"]})


if __name__ == "__main__":
    unittest.main()
