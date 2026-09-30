"""Initialize the native CSRF token before a new session reaches Desk.

Concurrent first Desk requests otherwise both generate and force-persist a
token. With MariaDB snapshot isolation one of those writes can raise 1020.
Frappe's before_request hook runs after HTTPRequest validates the incoming CSRF
token and before the login response sends cookies. Session-creation hooks run
before that validation and must not initialize a new token for a login POST.
Existing sessions, CSRF validation, transaction isolation and business writes
continue to use Frappe's normal behavior.
"""

import frappe


def initialize_csrf():
    login_manager = getattr(frappe.local, "login_manager", None)
    if (not login_manager or login_manager.resume is not False
            or frappe.session.user == "Guest"):
        return

    from frappe.sessions import get_csrf_token

    get_csrf_token()
