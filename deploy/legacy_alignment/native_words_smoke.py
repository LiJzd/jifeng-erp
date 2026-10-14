"""Real material-shape controls, permission checks, receipts and full rollback."""

import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4


ROOT = Path("/home/jifengtest/jifeng-dev/legacy-alignment-20261015-r1")
SITE = "jifeng-legacy-alignment-20261015-r1.localhost"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def main():
    sys.path.insert(0, str(ROOT / "dependencies"))
    sys.path.insert(0, str(ROOT / "app"))
    os.chdir(ROOT / "bench/sites")
    import frappe
    from jifeng_erp import inventory_words as words
    from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only

    frappe.init(site=SITE)
    frappe.connect()
    assert frappe.conf.db_name == "jf_legacy_align_20261015_r1" and not frappe.in_test
    frappe.set_user("Administrator")
    prefix, point = "WORD-NATIVE-" + uuid4().hex[:8], "word_" + uuid4().hex
    tables = ("User", "Has Role", "__Auth", "DefaultValue", words.DICTIONARY, words.category.RECEIPT,
              "Version", "Comment", "Deleted Document", "Series", "Item", "File", "GL Entry", "Stock Ledger Entry")

    def snapshot():
        return {name: digest(sorted(digest(row) for row in frappe.db.sql(
            "SELECT * FROM `" + (name if name.startswith("__") else "tab" + name) + "`", as_dict=True))) for name in tables}

    before, checks, result = snapshot(), [], {}
    _begin_rollback_only(point)
    failure = None
    try:
        company, other = frappe.get_all("Company", pluck="name", order_by="name")
        user = frappe.get_doc({"doctype": "User", "email": prefix.lower() + "@example.invalid", "first_name": "Shape Native QA",
                               "send_welcome_email": 0, "roles": [{"role": "Stock Manager"}, {"role": "Stock User"}]})
        user.insert()
        frappe.set_user(user.name)
        init_before = snapshot()
        initialized = words.init_dictionary(company=company)
        assert initialized["can_write"] and initialized["can_pinyin"]
        assert initialized["number_preview"].isdigit() and len(initialized["number_preview"]) >= 3
        assert snapshot() == init_before
        checks.append("normal_stock_manager_init_and_preview_do_not_reserve_numbers")
        legacy = frappe.get_doc({"doctype": words.DICTIONARY, "company": company, "archive_kind": "材料",
            "dictionary_type": "形状", "legacy_code": "016", "value": prefix + "-original", "inactive": 0})
        legacy.insert()
        payload = {"company": company, "dictionary_type": "形状", "request_id": str(uuid4()), "numbering_mode": words.MODE,
                   "value": "形状 & 字符 < 2", "remarks": "备注\n第二行", "inactive": 0}
        held = snapshot()
        preview = words.preview_change(payload)
        assert snapshot() == held
        saved = words.save_dictionary(payload)
        archive = saved["archives"][0]
        assert archive["legacy_number"] == "017" and archive["remarks"] == "备注\n第二行"
        assert archive["change_request_id"] == payload["request_id"]
        assert words.save_dictionary(payload) == saved
        assert words.resolve_change(payload["request_id"], company, preview["digest"])["archives"][0] == archive
        checks.append("native_create_number_notes_and_exact_receipt_replay")
        held = snapshot()
        try:
            words.save_dictionary({**payload, "value": "changed"})
        except frappe.ValidationError:
            assert snapshot() == held
            checks.append("different_content_cannot_reuse_request")
        else:
            raise AssertionError("A different shape reused a request")
        html = words.print_dictionary(company, [{"name": archive["name"], "version": archive["version"]}])["html"]
        assert "&amp;" in html and "&lt;" in html and "备注\n第二行" in html
        assert snapshot() == held
        checks.append("native_print_permissions_versions_and_html_escaping")
        update = {"company": company, "dictionary_type": "形状", "request_id": str(uuid4()), "name": archive["name"],
                  "version": archive["version"], "value": "花朵/植物", "inactive": 1}
        changed = words.save_dictionary(update)["archives"][0]
        assert changed["remarks"] == "备注\n第二行" and changed["inactive"] == 1
        assert words.read_dictionary(changed["name"])["legacy_number"] == "017"
        assert "change_request_id" not in words.resolve_change(payload["request_id"], company, preview["digest"])["archives"][0]
        checks.append("native_edit_retains_notes_number_and_rejects_old_receipt_as_current")
        held = snapshot()
        try:
            words.save_dictionary({**update, "request_id": str(uuid4()), "value": "stale"})
        except frappe.TimestampMismatchError:
            assert snapshot() == held
            checks.append("stale_edit_is_atomic")
        else:
            raise AssertionError("A stale shape edit succeeded")
        pinyin = words.preview_pinyin(company)["rows"]
        assert next(row for row in pinyin if row["name"] == changed["name"])["abbreviation"] == "HD/ZW"
        pinyin_payload = {"company": company, "dictionary_type": "形状", "request_id": str(uuid4()), "operation": "pinyin", "rows": pinyin}
        applied = words.save_dictionary(pinyin_payload)
        fresh = next(row for row in applied["archives"] if row["name"] == changed["name"])
        assert fresh["abbreviation"] == "HD/ZW" and fresh["inactive"] == 1
        assert words.save_dictionary(pinyin_payload) == applied
        checks.append("real_pinyin_updates_scoped_inactive_rows_and_replays_once")
        rows = words.query_dictionary(company, search="017")["rows"]
        assert len(rows) == 1 and rows[0]["name"] == fresh["name"]
        assert not words.query_dictionary(other, search="017")["rows"]
        try:
            words.save_dictionary({**update, "request_id": str(uuid4()), "company": other, "version": fresh["version"]})
        except frappe.PermissionError:
            checks.append("query_and_write_company_scope_are_exact")
        else:
            raise AssertionError("An archive from another company was changed")
        meta, mask = frappe.get_meta(words.DICTIONARY), None
        field = meta.get_field("notes")
        mask = field.mask
        field.mask = 1
        try:
            masked = words.read_dictionary(fresh["name"])
            assert "remarks" not in masked and "remarks" not in masked["field_meta"]
            try:
                words.save_dictionary({"company": company, "dictionary_type": "形状", "request_id": str(uuid4()),
                    "name": fresh["name"], "version": fresh["version"], "remarks": "blind overwrite"})
            except frappe.PermissionError:
                checks.append("native_masks_remove_notes_and_deny_blind_write")
            else:
                raise AssertionError("A masked shape field was changed")
        finally:
            field.mask = mask
        deletion = {"company": company, "dictionary_type": "形状", "request_id": str(uuid4()), "operation": "delete",
                    "name": fresh["name"], "version": fresh["version"]}
        deleted = words.save_dictionary(deletion)
        assert deleted["deleted"] == [fresh["name"]] and not frappe.db.exists(words.DICTIONARY, fresh["name"])
        assert words.save_dictionary(deletion) == deleted
        checks.append("native_delete_and_protected_receipt_replay")
        held = snapshot()
        try:
            words.save_dictionary({**payload, "request_id": str(uuid4()), "value": legacy.value})
        except frappe.UniqueValidationError:
            assert snapshot() == held
            checks.append("native_duplicate_name_rolls_back_number_and_receipt")
        else:
            raise AssertionError("A duplicate shape name was created")
        frappe.set_user("Administrator")
        viewer = frappe.get_doc({"doctype": "User", "email": prefix.lower() + "-reader@example.invalid", "first_name": "Shape Reader QA",
            "send_welcome_email": 0, "roles": [{"role": "Stock User"}]})
        viewer.insert()
        frappe.set_user(viewer.name)
        assert not words.init_dictionary(company=company)["can_write"]
        for action in (lambda: words.save_dictionary({**payload, "request_id": str(uuid4())}), lambda: words.preview_pinyin(company)):
            try:
                action()
            except frappe.PermissionError:
                pass
            else:
                raise AssertionError("Readonly Stock User changed shapes")
        assert words.query_dictionary(company, search=prefix)["rows"]
        checks.append("stock_user_reads_but_cannot_create_or_update_pinyin")
    except Exception as error:
        failure = error
        result.update(error_type=type(error).__name__, error_message=str(error))
    finally:
        frappe.set_user("Administrator")
        _end_rollback_only(point)
        frappe.db.rollback()
        result.update(checks=checks, rollback_values_unchanged=snapshot() == before, native=True)
        result["passed"] = failure is None and result["rollback_values_unchanged"]
        path = ROOT / (prefix.lower() + ".private.json")
        descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        frappe.destroy()
    print(json.dumps({key: result[key] for key in ("passed", "checks", "rollback_values_unchanged")}, ensure_ascii=False))
    if failure:
        raise failure
    assert result["passed"]


if __name__ == "__main__":
    main()
