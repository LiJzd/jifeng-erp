"""A normal Desk reload must request changed direct assets despite static caching."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1] / "jifeng_erp"


def load_hooks(path):
    spec = importlib.util.spec_from_file_location("asset_version_hooks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestAssetVersions(unittest.TestCase):
    def test_all_desk_assets_keep_paths_and_order_with_valid_content_revisions(self):
        hooks = load_hooks(ROOT / "hooks.py")
        self.assertEqual((len(hooks.app_include_css), len(hooks.app_include_js)), (15, 25))
        self.assertTrue(urlsplit(hooks.app_include_js[0]).path.endswith("jifeng_classic_bridge.js"))
        self.assertLess(next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_inventory_catalog.js?" in u),
                        next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_inventory_archive_list.js?" in u))
        self.assertLess(next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_inventory_catalog.js?" in u),
                        next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_inventory_tracking.js?" in u))
        self.assertLess(next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_product_dictionary.js?" in u),
                        next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_customer_type.js?" in u))
        self.assertLess(next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_product_dictionary.js?" in u),
                        next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_order_dictionary.js?" in u))
        self.assertLess(next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_order_dictionary.js?" in u),
                        next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_plating_color.js?" in u))
        self.assertLess(next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_plating_color.js?" in u),
                        next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_workspace_core.js?" in u))
        self.assertLess(next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_order_operations.js?" in u),
                        next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_workspace_core.js?" in u))
        self.assertLess(next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_customer_archive.js?" in u),
                        next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_workspace_core.js?" in u))
        self.assertLess(next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_salary_history.js?" in u),
                        next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_workspace_core.js?" in u))
        for component in ("jifeng_procurement_fulfillment", "jifeng_supplier_payables", "jifeng_inventory_tracking"):
            self.assertLess(next(i for i, u in enumerate(hooks.app_include_js) if component + ".js?" in u),
                            next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_workspace_core.js?" in u))
        self.assertLess(next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_tool_costs.js?" in u),
                        next(i for i, u in enumerate(hooks.app_include_js) if "jifeng_tool_archive.js?" in u))
        for url in hooks.app_include_css + hooks.app_include_js:
            parsed = urlsplit(url)
            self.assertFalse(parsed.netloc)
            self.assertTrue(parsed.path.startswith("/assets/jifeng_erp/"))
            self.assertTrue((ROOT / "public" / parsed.path.removeprefix("/assets/jifeng_erp/")).is_file())
            query = parse_qs(parsed.query)
            self.assertEqual(set(query), {"v"})
            self.assertRegex(query["v"][0], r"^[0-9a-f]{16}$")

    def test_same_length_asset_change_invalidates_only_that_cached_url(self):
        with tempfile.TemporaryDirectory(prefix="jifeng-asset-version-") as temporary:
            target = Path(temporary).resolve()
            shutil.copyfile(ROOT / "hooks.py", target / "hooks.py")
            original = load_hooks(ROOT / "hooks.py")
            for url in original.app_include_css + original.app_include_js:
                relative = urlsplit(url).path.removeprefix("/assets/jifeng_erp/")
                destination = target / "public" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / "public" / relative, destination)
            before = load_hooks(target / "hooks.py")
            core = target / "public/js/jifeng_workspace_core.js"
            content = core.read_bytes()
            core.write_bytes(content[:-1] + (b"X" if content[-1:] != b"X" else b"Y"))
            after = load_hooks(target / "hooks.py")
            self.assertEqual(before.app_include_css, after.app_include_css)
            self.assertEqual([urlsplit(u).path for u in before.app_include_js],
                             [urlsplit(u).path for u in after.app_include_js])
            changed = [(old, new) for old, new in zip(before.app_include_js, after.app_include_js) if old != new]
            self.assertEqual(len(changed), 1)
            self.assertTrue(urlsplit(changed[0][0]).path.endswith("jifeng_workspace_core.js"))
            self.assertEqual(before.before_request, after.before_request)


if __name__ == "__main__":
    unittest.main()
