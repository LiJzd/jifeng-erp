"""Initialize a new test site once; passwords stay in mounted secret files."""
import json
import os
from pathlib import Path

SITE = "jifeng-erp-test.localhost"
DB_NAME = "jifeng_erp_test"

def main():
    os.chdir("/home/frappe/frappe-bench/sites")
    site_path = Path(SITE)
    if site_path.exists():
        raise RuntimeError("Test site exists; inspect its state before proceeding")
    common = Path("common_site_config.json")
    current = json.loads(common.read_text())
    current.update(db_host="db", db_port=3306, redis_cache="redis://redis-cache:6379", redis_queue="redis://redis-queue:6379", redis_socketio="redis://redis-queue:6379", socketio_port=9000, mute_emails=True, host_name="https://116.62.241.148")
    common.write_text(json.dumps(current, indent=2) + "\n")
    import frappe
    from frappe.installer import _new_site
    def password(name):
        return Path("/run/secrets", name).read_text().strip()
    try:
        _new_site(db_name=DB_NAME, site=SITE, db_root_username="root", db_root_password=password("db_root_password"), db_password=password("site_db_password"), admin_password=password("admin_password"), db_type="mariadb", db_host="db", db_port=3306, mariadb_user_host_login_scope="%", install_apps=["erpnext", "hrms", "jifeng_erp"])
        assert frappe.local.site == SITE and frappe.conf.db_name == DB_NAME
        assert frappe.get_installed_apps() == ["frappe", "erpnext", "hrms", "jifeng_erp"]
        assert frappe.db.exists("Page", "jifeng-modern") and frappe.db.exists("Page", "jifeng-legacy")
        frappe.db.commit()
        Path(SITE, "test-initialization.json").write_text(json.dumps({"site":SITE,"database":DB_NAME,"installed_apps":frappe.get_installed_apps(),"source_database_imported":False,"original_sessions_imported":False,"both_pages_registered":True}, indent=2) + "\n")
        print("New shared test site initialized; both workspaces registered")
    finally:
        frappe.destroy()

if __name__ == "__main__":
    main()
