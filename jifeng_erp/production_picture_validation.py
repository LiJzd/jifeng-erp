"""Rollback-only picture-report checks with explicitly synthetic QA PNG uploads."""

import struct
import zlib

import frappe
from frappe.utils import today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only


def _qa_png(marker):
    """Generate a blue/orange checker PNG, never an actual business product image.

    A tEXt chunk identifies the unique run and fixture. Besides documenting its
    QA origin, the marker makes every upload's content hash unique so Frappe
    cannot deduplicate it to an existing business upload.
    """
    width, height = 32, 24

    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)

    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)  # PNG filter: none.
        for x in range(width):
            scanlines.extend((37, 99, 235, 255) if (x // 4 + y // 4) % 2 else (245, 158, 11, 255))
    return b"\x89PNG\r\n\x1a\n" + b"".join([
        chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
        chunk(b"tEXt", b"Description\x00QA SYNTHETIC - NOT A BUSINESS PRODUCT IMAGE - " + marker.encode("ascii")),
        chunk(b"IDAT", zlib.compress(bytes(scanlines))), chunk(b"IEND", b""),
    ])


def smoke(company, item_group, stock_uom):
    """Run only on an installed test site; remove physical QA files before rollback.

    File uploads are not transactional. Every fixture is tracked before insert,
    removed by Administrator through the native File.delete lifecycle, and
    checked on disk. Cleanup failures are raised together with a test failure.
    """
    frappe.only_for("System Manager")
    from frappe.desk.query_report import run
    from frappe.utils.file_manager import get_content_hash

    from jifeng_erp.production_picture import production_picture

    point = "jf_picture_smoke"
    original_user = frappe.session.user
    files, cleanup_errors = [], []
    primary_error, result, operator = None, None, None
    _begin_rollback_only(point)
    try:
        try:
            before_ledger = {doctype: frappe.db.count(doctype) for doctype in ("GL Entry", "Stock Ledger Entry")}
            suffix = frappe.generate_hash(length=12)
            prefix = "JF-QA-PICTURE-" + suffix + "-"
            raw, finished = [frappe.get_doc({
                "doctype": "Item", "item_code": prefix + kind,
                "item_name": "QA合成图片验收（非业务商品） " + kind,
                "item_group": item_group, "stock_uom": stock_uom,
            }).insert() for kind in ("RAW", "FG")]
            bom = frappe.get_doc({"doctype": "BOM", "company": company, "item": finished.name,
                                  "quantity": 1, "items": [{"item_code": raw.name, "qty": 1, "rate": 10}]}).insert()
            bom.submit()
            visible_flow, hidden_flow = [frappe.get_doc({
                "doctype": "Jifeng Flow Order", "company": company, "item_code": finished.name,
                "planned_qty": 5, "bom": bom.name,
            }).insert() for _ in range(2)]
            work = frappe.get_doc("Work Order", visible_flow.make_work_order())
            assert work.jf_flow_order == visible_flow.name and work.production_item == finished.name and work.qty == 5

            def upload(kind, attached_doc, private):
                payload = _qa_png(suffix + "-" + kind)
                digest = get_content_hash(payload)
                assert not frappe.db.exists("File", {"content_hash": digest}), "QA fixture content must be unique"
                file = frappe.get_doc({
                    "doctype": "File", "file_name": prefix + kind + ".png", "content": payload,
                    "is_private": private, "attached_to_doctype": attached_doc.doctype,
                    "attached_to_name": attached_doc.name,
                })
                files.append(file)  # Also track a partially completed insert for cleanup.
                file.insert()
                assert file.file_name.startswith(prefix) and file.content_hash == digest
                assert file.exists_on_disk() and file.get_content() == payload
                assert frappe.db.count("File", {"content_hash": digest}) == 1, "QA uploads cannot share business blobs"
                return file

            authorized = upload("AUTHORIZED-PRIVATE", visible_flow, 1)
            secret = upload("DENIED-PRIVATE", hidden_flow, 1)
            fallback = upload("ITEM-PUBLIC", finished, 0)
            operator = frappe.get_doc({
                "doctype": "User", "email": f"jf-picture-{suffix}@example.invalid",
                "first_name": "计划图片验收", "send_welcome_email": 0,
                "roles": [{"role": "Manufacturing User"}],
            }).insert()
            for doctype, name in (("Company", company), ("Work Order", work.name),
                                  ("Jifeng Flow Order", visible_flow.name), ("Item", finished.name)):
                frappe.get_doc({"doctype": "User Permission", "user": operator.name, "allow": doctype,
                                "for_value": name, "apply_to_all_doctypes": 1}).insert()

            filters = {"company": company, "from_date": today(), "to_date": today(),
                       "flow_order": visible_flow.name}

            def set_images(flow_image, item_image):
                frappe.set_user("Administrator")
                # Flow.validate refreshes its image from Item. Direct writes here
                # model an older flow snapshot after the Item picture has changed.
                frappe.db.set_value("Jifeng Flow Order", visible_flow.name, "image", flow_image)
                frappe.db.set_value("Item", finished.name, "image", item_image)
                frappe.clear_document_cache("Jifeng Flow Order", visible_flow.name)
                frappe.clear_document_cache("Item", finished.name)
                frappe.set_user(operator.name)

            def only_row():
                columns, rows = production_picture(filters)
                assert len(rows) == 1 and rows[0]["name"] == work.name
                assert rows[0]["qty"] == 5 and rows[0]["stock_uom"] == stock_uom
                assert columns[0]["fieldname"] == "image_url"
                return rows[0]

            set_images(authorized.file_url, "")
            assert frappe.get_doc("Jifeng Flow Order", visible_flow.name).has_permission("read")
            assert not frappe.get_doc("Jifeng Flow Order", hidden_flow.name).has_permission("read")
            assert frappe.get_list("Jifeng Flow Order", filters={"name": hidden_flow.name}, pluck="name") == []
            assert authorized.is_downloadable() and not secret.is_downloadable()
            row = only_row()
            assert row["image_url"] == authorized.unique_url and "?fid=" in row["image_url"]
            assert row["image_status"] == "已上传" and row["image_source"] == "流程单"
            api_result = run("Jifeng Production Picture", filters=filters)["result"]
            assert len(api_result) == 1 and api_result[0]["image_url"] == authorized.unique_url

            set_images(secret.file_url, "")
            row = only_row()
            assert row["image_url"] == "" and row["image_status"] == "无附件权限"
            assert secret.file_url not in repr(row), "Denied file URL cannot appear in the report response"
            set_images(secret.file_url, fallback.file_url)
            row = only_row()
            assert row["image_url"] == fallback.unique_url and row["image_source"] == "商品档案"
            assert secret.file_url not in repr(row)
            set_images("", "")
            row = only_row()
            assert row["image_url"] == "" and row["image_status"] == "未上传"

            frappe.set_user("Guest")
            try:
                production_picture(filters)
            except frappe.PermissionError:
                pass
            else:
                raise AssertionError("Guest cannot read production pictures")
            assert not authorized.is_downloadable() and not secret.is_downloadable()
            frappe.set_user("Administrator")
            assert {doctype: frappe.db.count(doctype) for doctype in before_ledger} == before_ledger
            result = {"passed": True, "checks": [
                "native_flow_work_order", "manufacturing_user_source_permissions", "private_attachment_native_permissions",
                "authorized_private_image_with_fid", "report_api_role_access", "denied_attachment_url_not_returned",
                "readable_item_image_fallback", "missing_image_label", "guest_denied", "quantities_keep_stock_unit",
                "no_stock_or_financial_posting", "unique_synthetic_png_content", "native_file_cleanup",
            ], "business_data": "rolled_back", "physical_files_cleaned": False,
                "image_content": "QA合成32×24蓝橙棋盘PNG，tEXt标记唯一验收批次；不是实际业务商品图片"}
        except Exception as exc:
            primary_error = exc
    finally:
        try:
            frappe.set_user("Administrator")
            for file in reversed(files):
                try:
                    if file.name and frappe.db.exists("File", file.name):
                        file.delete(ignore_permissions=True)
                    if file.file_url and file.exists_on_disk():
                        raise AssertionError("QA upload remains on disk after native File.delete: " + file.file_name)
                    if file.name and frappe.db.exists("File", file.name):
                        raise AssertionError("QA File row remains after native File.delete: " + file.name)
                except Exception as exc:
                    cleanup_errors.append(exc)
        except Exception as exc:
            cleanup_errors.append(exc)
        try:
            _end_rollback_only(point)
        except Exception as exc:
            cleanup_errors.append(exc)
        try:
            frappe.set_user(original_user)
            if operator:
                frappe.clear_cache(user=operator.name)
        except Exception as exc:
            cleanup_errors.append(exc)

    if cleanup_errors:
        failures = ([primary_error] if primary_error else []) + cleanup_errors
        raise ExceptionGroup("计划图片验收或QA文件清理失败；不能视作清理完成", failures)
    if primary_error:
        raise primary_error.with_traceback(primary_error.__traceback__)
    result["physical_files_cleaned"] = True
    return result
