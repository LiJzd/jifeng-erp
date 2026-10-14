"""One-use synthetic GUI user and dictionary examples in the local clone only."""

import json
import os
from pathlib import Path
import secrets
import sys
from uuid import uuid4


ROOT = Path("/home/jifengtest/jifeng-dev/legacy-alignment-20261015-r1")
SITE = "jifeng-legacy-alignment-20261015-r1.localhost"
DATABASE = "jf_legacy_align_20261015_r1"


def write_new(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)


def main():
    assert not (ROOT / "gui-seed.intent.private.json").exists(), "Inspect an existing seed instead of repeating"
    sys.path.insert(0, str(ROOT / "app"))
    os.chdir(ROOT / "bench/sites")
    import frappe
    from frappe.utils.password import update_password
    from jifeng_erp import inventory_category as category, inventory_archive as archive

    frappe.init(site=SITE)
    frappe.connect()
    frappe.set_user("Administrator")
    assert frappe.conf.db_name == DATABASE
    username = "legacy-alignment-qa@example.invalid"
    assert not frappe.db.exists("User", username)
    company = frappe.db.get_value("Company", {"name": ["like", "%Validation%"]}, "name")
    assert company
    write_new(ROOT / "gui-seed.intent.private.json", {"site": SITE, "synthetic": True,
        "targets": ["User", "Has Role", "DefaultValue", archive.DICTIONARY, category.RECEIPT]})
    password = secrets.token_urlsafe(30)
    user = frappe.get_doc({"doctype": "User", "email": username, "first_name": "本地对照 QA", "language": "zh",
        "send_welcome_email": 0, "roles": [{"role": role} for role in ("System Manager", "Stock Manager", "Stock User", "Item Manager")
                                           if frappe.db.exists("Role", role)]})
    user.insert()
    update_password(username, password, logout_all_sessions=False)
    frappe.defaults.set_user_default("Company", company, user=username)
    records = []
    for kind in ("物料", "材料"):
        parent, child = str(uuid4()), str(uuid4())
        result = category.save_batch({"kind": kind, "company": company, "request_id": str(uuid4()), "rows": [
            {"row_id": parent, "record_type": "major", "operation": "upsert", "values": {"value": "界面对照" + kind,
                "legacy_code": "0009", "owner_name": "对照负责", "region": "测试区域", "pinyin": "JMDZ"}},
            {"row_id": child, "record_type": "minor", "operation": "upsert", "parent_row_id": parent,
                "values": {"value": "对照小类", "material": "测试材质", "display_uom": "Nos",
                           "conversion_uom_text": "Nos", "conversion_value": "1.2500", "pinyin": "DZXL"}}]})
        records.append(result)
    frappe.set_user(username)
    options = archive.init_archive("物料", company)
    frappe.set_user("Administrator")
    frappe.db.commit()
    write_new(ROOT / "gui-user.private.json", {"username": username, "password": password,
        "site": SITE, "url": "http://legacy-alignment.localhost:18868", "company": company,
        "can_create_item": options["can_create_item"]})
    write_new(ROOT / "gui-seed.done.private.json", {"site": SITE, "synthetic": True,
        "category_batches": records, "can_create_item": options["can_create_item"]})
    print(json.dumps({"synthetic_gui_seed": True, "kinds": ["物料", "材料"], "can_create_item": options["can_create_item"]}))
    frappe.destroy()


if __name__ == "__main__":
    main()
