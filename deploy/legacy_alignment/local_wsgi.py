"""Serve the exact candidate clone on loopback; no debugger or source reload."""

import os
from pathlib import Path
import sys


ROOT = Path("/home/jifengtest/jifeng-dev/legacy-alignment-20261015-r1")
sys.path.insert(0, str(ROOT / "dependencies"))
sys.path.insert(0, str(ROOT / "app"))
os.chdir(ROOT / "bench/sites")

import frappe.app  # noqa: E402 - select the isolated source/site before Frappe imports
from werkzeug.serving import run_simple  # noqa: E402


frappe.app._site = "jifeng-legacy-alignment-20261015-r1.localhost"
frappe.app._sites_path = str(ROOT / "bench/sites")
application = frappe.app.application_with_statics()
application.debug = False

if __name__ == "__main__":
    run_simple("127.0.0.1", 18868, application, threaded=True, use_reloader=False,
               use_debugger=False, use_evalex=False)
