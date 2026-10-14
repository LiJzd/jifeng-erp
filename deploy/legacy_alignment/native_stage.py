"""Install only three inventory schemas in the exact one-use local clone."""

import hashlib
import json
import os
from pathlib import Path
import sys


ROOT = Path("/home/jifengtest/jifeng-dev/legacy-alignment-20261015-r1")
SITE = "jifeng-legacy-alignment-20261015-r1.localhost"
DATABASE = "jf_legacy_align_20261015_r1"
TARGETS = ("Jifeng Inventory Profile", "Jifeng Inventory Dictionary", "Jifeng Inventory Category Batch")


def write_new(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)


def hash_value(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def main():
    assert (ROOT / "clone.done.private.json").is_file()
    assert not (ROOT / "schema.intent.private.json").exists(), "Inspect any previous schema intent instead of repeating"
    sys.path.insert(0, str(ROOT / "app"))
    os.chdir(ROOT / "bench/sites")
    (ROOT / "bench/logs").mkdir(mode=0o700, exist_ok=True)
    import frappe

    frappe.init(site=SITE)
    frappe.connect()
    frappe.set_user("Administrator")
    assert frappe.conf.db_name == DATABASE and not frappe.conf.developer_mode
    before, planned = {}, {}
    try:
        for name in TARGETS:
            folder = name.lower().replace(" ", "_")
            path = ROOT / "app/jifeng_erp/jifeng_erp/doctype" / folder / (folder + ".json")
            planned[name] = json.loads(path.read_text())
            existing = frappe.get_doc("DocType", name).as_dict() if frappe.db.exists("DocType", name) else None
            before[name] = {"document": existing,
                "custom_permissions": frappe.get_all("Custom DocPerm", filters={"parent": name}, fields=["*"]),
                "rows": frappe.db.sql("SELECT * FROM `tab" + name + "` ORDER BY name", as_dict=True) if existing else []}
        write_new(ROOT / "schema.before.private.json", before)
        write_new(ROOT / "schema.intent.private.json", {"site": SITE, "database": DATABASE,
            "targets": TARGETS, "before_sha256": hash_value(before),
            "planned_sha256": {name: hash_value(value) for name, value in planned.items()},
            "ddl_is_not_atomic": True, "old_sites_untouched": True})
        old_import, old_patch = frappe.flags.in_import, frappe.flags.in_patch
        try:
            frappe.flags.in_import, frappe.flags.in_patch = True, True
            after = {}
            for name in TARGETS:
                spec = planned[name]
                if before[name]["document"]:
                    document = frappe.get_doc("DocType", name)
                    old_fields = {field.fieldname: field.name for field in document.fields}
                    old_permissions = {(row.role, row.permlevel or 0): row.name for row in document.permissions}
                    present = {field.fieldname: field for field in document.fields}
                    assert set(present).issubset({field["fieldname"] for field in spec["fields"]})
                    for field in spec["fields"]:
                        if field["fieldname"] in present:
                            present[field["fieldname"]].update(field)
                        else:
                            document.append("fields", field)
                    order = {field: index for index, field in enumerate(spec["field_order"], 1)}
                    document.fields.sort(key=lambda field: order[field.fieldname])
                    for index, field in enumerate(document.fields, 1):
                        field.idx = index
                    permissions = {(row.role, row.permlevel or 0): row for row in document.permissions}
                    assert set(permissions) == {(row["role"], row.get("permlevel", 0)) for row in spec["permissions"]}
                    for row in spec["permissions"]:
                        permissions[row["role"], row.get("permlevel", 0)].update(row)
                    document.save()
                    current = frappe.get_doc("DocType", name)
                    assert all(next(field for field in current.fields if field.fieldname == key).name == value for key, value in old_fields.items())
                    assert {(row.role, row.permlevel or 0): row.name for row in current.permissions} == old_permissions
                else:
                    document = frappe.get_doc(spec)
                    document.insert()
                frappe.db.commit()
                frappe.clear_cache(doctype=name)
                current = frappe.get_doc("DocType", name)
                actual = {field.fieldname: field for field in current.fields}
                for field in spec["fields"]:
                    for key, value in field.items():
                        assert actual[field["fieldname"]].get(key) == value, "Installed field mismatch: " + name + "/" + field["fieldname"] + "/" + key
                assert hash_value(frappe.get_all("Custom DocPerm", filters={"parent": name}, fields=["*"])) == hash_value(before[name]["custom_permissions"])
                # Added optional fields may fill null/default values; every old
                # business column and exact row identity must retain its value.
                rows = frappe.db.sql("SELECT * FROM `tab" + name + "` ORDER BY name", as_dict=True)
                assert len(rows) == len(before[name]["rows"])
                for previous, result in zip(before[name]["rows"], rows):
                    assert all(result[key] == value for key, value in previous.items())
                after[name] = {"fields": len(current.fields), "rows": len(rows),
                    "old_business_values_preserved": True, "custom_permissions_preserved": True,
                    "old_child_ids_preserved": bool(before[name]["document"])}
                write_new(ROOT / ("schema." + name.lower().replace(" ", "_") + ".done.private.json"), after[name])
            write_new(ROOT / "schema.done.private.json", {"site": SITE, "targets": after})
            print(json.dumps({"installed_in_clone": True, "targets": after}))
        finally:
            frappe.flags.in_import, frappe.flags.in_patch = old_import, old_patch
    finally:
        frappe.db.rollback()
        frappe.destroy()


if __name__ == "__main__":
    main()
