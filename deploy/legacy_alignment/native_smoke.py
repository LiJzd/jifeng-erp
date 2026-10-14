"""Actual Frappe/SQL inventory checks, synthetic rows, and native rollback."""

import base64
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from uuid import uuid4


ROOT = Path("/home/jifengtest/jifeng-dev/legacy-alignment-20261015-r1")
SITE = "jifeng-legacy-alignment-20261015-r1.localhost"
DATABASE = "jf_legacy_align_20261015_r1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def main():
    assert (ROOT / "schema.done.private.json").exists()
    sys.path.insert(0, str(ROOT / "dependencies"))
    sys.path.insert(0, str(ROOT / "app"))
    os.chdir(ROOT / "bench/sites")
    import frappe
    from PIL import Image
    from jifeng_erp import inventory_archive as archive, inventory_category as category, inventory_catalog as catalog
    from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only

    frappe.init(site=SITE)
    frappe.connect()
    frappe.set_user("Administrator")
    assert frappe.conf.db_name == DATABASE and not frappe.in_test
    prefix = "LEGACY-NATIVE-" + uuid4().hex[:8]
    point = "legacy_alignment_" + uuid4().hex
    tables = ("User", "Has Role", "__Auth", "Item", "Item Default", "UOM Conversion Detail",
              archive.PROFILE, archive.DICTIONARY, category.RECEIPT, "File", "Version", "Comment",
              "GL Entry", "Stock Ledger Entry", "Series")
    def snapshot():
        result = {}
        for name in tables:
            table = name if name.startswith("__") else "tab" + name
            rows = frappe.db.sql("SELECT * FROM `" + table + "`", as_dict=True)
            result[name] = digest(sorted((digest(row) for row in rows)))
        return result
    before = snapshot()
    files_before = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (ROOT / "bench/sites" / SITE / "private/files").rglob("*") if path.is_file()}
    checks, result = [], {"site": SITE, "started_at": datetime.now(timezone.utc).isoformat(),
                          "app_source": str(Path(archive.__file__).resolve()), "native": True}
    _begin_rollback_only(point)
    error = None
    try:
        companies = frappe.get_all("Company", pluck="name", order_by="name")
        assert len(companies) == 2
        company, other_company = companies
        group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
        unit = "Nos" if frappe.db.exists("UOM", "Nos") else frappe.db.get_value("UOM", {}, "name")
        user = frappe.get_doc({"doctype": "User", "email": prefix.lower() + "@example.invalid",
            "first_name": "Native Inventory QA", "send_welcome_email": 0,
            "roles": [{"role": "Stock Manager"}, {"role": "Stock User"}]})
        user.insert()
        frappe.set_user(user.name)
        assert "Stock Manager" in frappe.get_roles() and "System Manager" not in frappe.get_roles()
        config = category.init_grid("物料", company)
        assert config["can_create"]
        checks.append("ordinary_stock_manager_initialization")
        parent_id, child_id = str(uuid4()), str(uuid4())
        payload = {"kind": "物料", "company": company, "request_id": str(uuid4()), "rows": [
            {"row_id": child_id, "record_type": "minor", "operation": "upsert", "parent_row_id": parent_id,
             "values": {"value": prefix + "-small", "material": "原文材质", "display_uom": "原文单位", "conversion_value": "1.2500"}},
            {"row_id": parent_id, "record_type": "major", "operation": "upsert",
             "values": {"value": prefix + "-large", "legacy_code": "0009", "owner_name": "负责原文", "region": "区域原文"}}]}
        saved = category.save_batch(payload)
        assert saved["status"] == "applied" and len(saved["rows"]) == 2
        assert category.save_batch(payload) == saved
        checks.append("parent_before_child_atomic_batch_and_receipt_replay")
        held = snapshot()
        bad_parent, bad_child = str(uuid4()), str(uuid4())
        try:
            category.save_batch({"kind": "物料", "company": company, "request_id": str(uuid4()), "rows": [
                {"row_id": bad_parent, "record_type": "major", "operation": "upsert", "values": {"value": prefix + "-rollback"}},
                {"row_id": bad_child, "record_type": "minor", "operation": "upsert", "parent_row_id": bad_parent, "values": {"value": " "}}]})
        except frappe.ValidationError:
            assert snapshot() == held
            checks.append("later_child_validation_rolls_back_parent_and_receipt")
        else:
            raise AssertionError("Native dictionary validation accepted a blank child")
        frappe.set_user("Administrator")
        receipt = frappe.get_doc(category.RECEIPT, payload["request_id"])
        receipt.result_json = "{}"
        try:
            receipt.save()
        except frappe.PermissionError:
            assert snapshot() == held
            checks.append("administrator_cannot_edit_batch_receipt")
        else:
            raise AssertionError("Batch receipt could be edited directly")
        frappe.set_user(user.name)
        major = next(row for row in saved["rows"] if row["row_id"] == parent_id)
        minor = next(row for row in saved["rows"] if row["row_id"] == child_id)
        selected = category.select_category("物料", company, minor["name"], minor["version"], major["version"])
        assert selected["profile"]["conversion_value"] == "1.2500"
        choices = category.query_selection("物料", company, search=prefix)
        assert any(row["name"] == minor["name"] for row in choices["rows"])
        checks.append("native_related_grid_and_category_picker")
        pinyin_before = snapshot()
        pinyin = category.preview_pinyin("物料", company, [{"row_id": str(uuid4()), "value": "界面对照物料"}])
        assert pinyin["rows"][0]["pinyin"] == "JMDZWL" and snapshot() == pinyin_before
        checks.append("real_pypinyin_preview_preserves_database_values")
        manager = user.name
        new_options = archive.init_archive("物料", company)
        result["stock_manager_item_create"] = new_options["can_create_item"]
        assert new_options["can_create_item"] == bool(frappe.has_permission("Item", "create"))
        assert new_options["can_link_item"]
        frappe.set_user("Administrator")
        native_item = frappe.get_doc({"doctype": "Item", "item_code": prefix + "-linked-item",
            "item_name": prefix + "-linked-item", "item_group": group, "stock_uom": unit,
            "is_stock_item": 1, "opening_stock": 0, "standard_rate": 0})
        native_item.insert()
        native_item_before = digest(frappe.db.sql("SELECT * FROM `tabItem` WHERE name=%s", (native_item.name,), as_dict=True))
        frappe.set_user(manager)
        linked = archive.init_archive("物料", company, existing_item=native_item.name)
        image = BytesIO()
        Image.new("RGB", (5, 4), "navy").save(image, format="PNG")
        picture = {"file_name": prefix + ".png", "content": base64.b64encode(image.getvalue()).decode()}
        creation = {"kind": "物料", "request_id": str(uuid4()),
            "profile": {"company": company, "legacy_number": prefix, "display_name": prefix,
                        "major_category": selected["profile"]["major_category"], "minor_category": selected["profile"]["minor_category"]},
            "item": {}, "existing_item": native_item.name, "item_version": linked["item_version"], "image_upload": picture,
            "image_change_request_id": str(uuid4())}
        created = archive.save_archive(creation)
        assert created["image"]["state"] == "已上传" and created["image_change_request_id"] == creation["image_change_request_id"]
        assert archive.save_archive(creation)["name"] == created["name"]
        assert archive.resolve_creation(creation["request_id"], "物料")["archive"]["image_change_request_id"] == creation["image_change_request_id"]
        assert digest(frappe.db.sql("SELECT * FROM `tabItem` WHERE name=%s", (native_item.name,), as_dict=True)) == native_item_before
        checks.append("manager_links_existing_item_with_private_native_file_and_replayed_creation")
        late_before = snapshot()
        callback_names = ("before_commit", "after_commit", "before_rollback", "after_rollback")
        callbacks_before = {name: tuple(getattr(frappe.db, name)._functions) for name in callback_names}
        private_path = ROOT / "bench/sites" / SITE / "private/files"
        late_files_before = {str(path.relative_to(private_path)): hashlib.sha256(path.read_bytes()).hexdigest()
                             for path in private_path.rglob("*") if path.is_file()}
        late_image = BytesIO()
        Image.new("RGB", (7, 3), "orange").save(late_image, format="PNG")
        try:
            archive.save_archive({"kind": "物料", "name": created["name"],
                "profile_version": created["profile_version"], "item_version": created["item_version"],
                "profile": {"display_name": "   "}, "image_change_request_id": str(uuid4()),
                "image_upload": {"file_name": prefix + "-late.png", "content": base64.b64encode(late_image.getvalue()).decode()}})
        except frappe.ValidationError:
            # The real File insert runs before profile.save rejects the name.
            # Verify the nested atomic cleanup before the final full rollback.
            assert snapshot() == late_before
            assert {name: tuple(getattr(frappe.db, name)._functions) for name in callback_names} == callbacks_before
            assert {str(path.relative_to(private_path)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in private_path.rglob("*") if path.is_file()} == late_files_before
            checks.append("late_archive_validation_restores_sql_file_and_existing_callbacks")
        else:
            raise AssertionError("Native profile validation accepted a blank name after File creation")
        frappe.set_user("Administrator")
        admin_creation = {**creation, "request_id": str(uuid4()), "item": {"item_group": group, "stock_uom": unit},
                          "existing_item": None, "item_version": None, "image_change_request_id": str(uuid4())}
        administrator_created = archive.save_archive(admin_creation)
        assert administrator_created["image"]["state"] == "已上传"
        checks.append("administrator_creates_native_item_and_archive_with_picture")
        frappe.set_user(manager)
        viewed = catalog.query_archives("物料", company, search=prefix, status="全部")
        assert viewed["rows"][0]["image"]["source_doctype"] == archive.PROFILE
        assert viewed["rows"][0]["image"]["state"] == "已上传"
        assert "image" not in viewed["rows"][0]["profile"]
        checks.append("three_view_projection_uses_exact_inventory_attachment")
        meta = frappe.get_meta(archive.PROFILE)
        field = meta.get_field("image")
        original_mask = field.mask
        field.mask = 1
        try:
            masked = archive.read_archive(created["name"], "物料", company)
            assert "image" not in masked and "image_change_request_id" not in masked
            checks.append("native_mask_permission_blocks_picture_and_receipt_projection")
        finally:
            field.mask = original_mask
        try:
            archive.read_archive(created["name"], "物料", other_company)
        except frappe.PermissionError:
            checks.append("wrong_company_read_denied")
        else:
            raise AssertionError("A different company accepted the archive")
        file_names = set(frappe.get_all("File", filters={"attached_to_name": created["name"]}, pluck="name"))
        updated = archive.save_archive({"kind": "物料", "name": created["name"], "profile_version": created["profile_version"],
            "item_version": created["item_version"], "profile": {"image": None}, "image_change_request_id": str(uuid4())})
        assert updated["image"]["state"] == "未上传"
        assert set(frappe.get_all("File", filters={"attached_to_name": created["name"]}, pluck="name")) == file_names
        checks.append("clear_keeps_native_private_file")
        frappe.set_user("Administrator")
        viewer = frappe.get_doc({"doctype": "User", "email": prefix.lower() + "-viewer@example.invalid",
            "first_name": "Native Readonly QA", "send_welcome_email": 0, "roles": [{"role": "Stock User"}]})
        viewer.insert()
        frappe.set_user(viewer.name)
        assert not category.init_grid("物料", company)["can_create"]
        readonly = archive.read_archive(created["name"], "物料", company)
        assert not readonly["can_write"] and not readonly["image_options"]["can_choose"]
        try:
            category.preview_pinyin("物料", company, [{"row_id": str(uuid4()), "value": "只读测试"}])
        except frappe.PermissionError:
            checks.append("stock_user_cannot_update_pinyin_preview")
        else:
            raise AssertionError("Readonly Stock User received writable pinyin preview")
        try:
            category.save_batch({"kind": "物料", "company": company, "request_id": str(uuid4()), "rows": [
                {"row_id": str(uuid4()), "record_type": "major", "operation": "upsert", "values": {"value": prefix + "-denied"}}]})
        except frappe.PermissionError:
            checks.append("stock_user_reads_but_cannot_modify_category_or_picture")
        else:
            raise AssertionError("Readonly Stock User created a category")
    except Exception as caught:
        error = caught
        result["error_type"] = type(caught).__name__
        result["error_message"] = str(caught)
    finally:
        frappe.set_user("Administrator")
        # The outer DB rollback is insufficient for native filesystem writes.
        # Native full rollback executes the File cleanup callback as well.
        _end_rollback_only(point)
        frappe.db.rollback()
        result["rollback_values_unchanged"] = snapshot() == before
        files_after = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in (ROOT / "bench/sites" / SITE / "private/files").rglob("*") if path.is_file()}
        result["private_files_unchanged"] = files_after == files_before
        result["checks"] = checks
        result["passed"] = error is None and result["rollback_values_unchanged"] and result["private_files_unchanged"]
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        target = ROOT / ("smoke-" + prefix.lower() + ".private.json")
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        frappe.destroy()
    print(json.dumps({key: result[key] for key in ("passed", "checks", "rollback_values_unchanged", "private_files_unchanged")}, ensure_ascii=False))
    if error is not None:
        raise error
    assert result["passed"]


if __name__ == "__main__":
    main()
