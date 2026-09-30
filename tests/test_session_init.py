"""The login token is initialized after CSRF validation, only for a new login."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class SessionInitializationTests(unittest.TestCase):
    def run_hook(self, user="Administrator", resume=False, manager=True):
        get_token = Mock(return_value="private-test-token")
        frappe = types.ModuleType("frappe")
        frappe.session = types.SimpleNamespace(user=user)
        frappe.local = types.SimpleNamespace()
        if manager:
            frappe.local.login_manager = types.SimpleNamespace(resume=resume)
        sessions = types.ModuleType("frappe.sessions")
        sessions.get_csrf_token = get_token
        path = Path(__file__).resolve().parents[1] / "jifeng_erp/session_init.py"
        spec = importlib.util.spec_from_file_location("tested_session_init", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"frappe": frappe, "frappe.sessions": sessions}):
            spec.loader.exec_module(module)
            module.initialize_csrf()
        return get_token

    def test_new_authenticated_login_uses_native_token(self):
        self.run_hook().assert_called_once_with()

    def test_resumed_session_preserves_token(self):
        self.run_hook(resume=True).assert_not_called()

    def test_guest_does_not_persist_token(self):
        self.run_hook(user="Guest").assert_not_called()

    def test_no_login_context_has_no_side_effect(self):
        self.run_hook(manager=False).assert_not_called()

    def test_hook_runs_after_native_incoming_csrf_check(self):
        path = Path(__file__).resolve().parents[1] / "jifeng_erp/hooks.py"
        spec = importlib.util.spec_from_file_location("tested_hooks", path)
        hooks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hooks)
        self.assertEqual(hooks.before_request, "jifeng_erp.session_init.initialize_csrf")
        self.assertFalse(hasattr(hooks, "on_session_creation"))


if __name__ == "__main__":
    unittest.main()
