"""Initialize the native CSRF token before a new session reaches Desk.

Concurrent first Desk requests otherwise both generate and force-persist a
token. With MariaDB snapshot isolation one of those writes can raise 1020.
The native login hook runs after Session creation and before cookies are sent.
Existing sessions, CSRF validation, transaction isolation and business writes
continue to use Frappe's normal behavior.
"""

import frappe


def initialize_csrf(login_manager=None):
    if frappe.session.user == "Guest":
        return

    from frappe.sessions import get_csrf_token

    get_csrf_token()
