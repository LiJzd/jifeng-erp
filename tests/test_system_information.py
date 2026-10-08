"""Read-only system-information endpoint contract."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[1]


class TestSystemInformation(unittest.TestCase):
	def setUp(self):
		self.frappe = types.ModuleType("frappe")
		self.frappe.session = types.SimpleNamespace(user="qa@example.invalid")
		self.frappe.PermissionError = PermissionError
		self.frappe.throw = lambda message, error=RuntimeError: (_ for _ in ()).throw(error(message))
		self.frappe.get_installed_apps = lambda: ["frappe", "erpnext", "hrms", "jifeng_erp"]
		self.decorator_options = []
		self.frappe.whitelist = lambda **options: (self.decorator_options.append(options) or (lambda fn: fn))
		packages = {
			"frappe": "16.1.0",
			"erpnext": "16.2.0",
			"hrms": "16.3.0",
			"jifeng_erp": "0.2.0",
		}
		self.frappe.__version__ = packages["frappe"]
		self.package_modules = {name: types.ModuleType(name) for name in packages if name != "frappe"}
		for name, version in packages.items():
			if name != "frappe":
				self.package_modules[name].__version__ = version
		self.patch = patch.dict(sys.modules, {"frappe": self.frappe, **self.package_modules})
		self.patch.start()
		self.addCleanup(self.patch.stop)
		spec = importlib.util.spec_from_file_location("system_information_under_test", ROOT / "jifeng_erp" / "system_information.py")
		self.module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(self.module)

	def test_get_is_read_only_scoped_to_versions_and_help(self):
		result = self.module.get_information()
		self.assertEqual(self.decorator_options, [{"methods": ["GET"]}])
		self.assertEqual(result["release"], "公网业务测试版")
		self.assertEqual([row["version"] for row in result["versions"]], ["16.1.0", "16.2.0", "16.3.0", "0.2.0"])
		self.assertEqual(len(result["modules"]), 19)
		self.assertTrue(all({"name", "path", "verified", "pending"} <= set(row) for row in result["modules"]))
		self.assertIn("同一业务数据", result["shared_data_note"])
		self.assertIn("续费合同", next(row["pending"] for row in result["modules"] if row["name"] == "关于"))
		self.assertNotIn("commit", str(result).lower())
		self.assertFalse({"path", "config", "database", "user", "password", "remote"} & set(result))

	def test_guest_is_rejected(self):
		self.frappe.session.user = "Guest"
		with self.assertRaises(PermissionError):
			self.module.get_information()
		self.frappe.get_installed_apps = lambda: self.fail("Guest must be rejected before reading installed apps")
		with self.assertRaises(PermissionError):
			self.module.get_information()

	def test_only_installed_app_versions_are_returned(self):
		self.frappe.get_installed_apps = lambda: ["frappe", "jifeng_erp"]
		self.assertEqual([row["name"] for row in self.module.get_information()["versions"]], ["Frappe", "集沣 ERP"])


if __name__ == "__main__":
	unittest.main()
