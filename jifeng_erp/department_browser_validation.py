"""Persistent, journaled synthetic fixtures for department/picture browser acceptance.

Only call from an isolated test-site bench console. Browser fixtures deliberately
commit so a separate browser connection can read them. Their private JSON journal
records every created ID and lifecycle phase; cleanup removes only these objects.
"""

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import frappe
from frappe.utils import add_days, flt, now, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.production_picture_validation import _qa_png


KINDS = {"items": "Item", "departments": "Department", "workstations": "Workstation",
         "operations": "Operation", "boms": "BOM", "flows": "Jifeng Flow Order",
         "work_orders": "Work Order", "job_cards": "Job Card", "files": "File"}


def _journal(data):
    match = re.fullmatch(r"JF-DEPT-BROWSER-([a-f0-9]{12})", data.get("prefix", ""))
    if not match or data.get("fixture_type") != "department_picture_browser":
        raise ValueError("不是受支持的部门图片QA夹具")
    root = Path(frappe.get_site_path("private", "jifeng_qa")).resolve()
    path = root / ("department-browser-" + match[1] + ".json")
    if data.get("artifact_path") and Path(data["artifact_path"]).resolve() != path:
        raise ValueError("QA记录路径与夹具身份不一致")
    root.mkdir(parents=True, exist_ok=True)
    data["artifact_path"] = str(path)
    data["updated_at"] = now()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _track(data, kind, doc):
    data[kind].append(doc.name)
    _journal(data)
    return doc


def _collect_files(data, objects=None):
    """Track native image clones sharing this fixture's unique content and URL."""
    candidates = list(objects or [])
    records = {row["name"]: row for row in data.get("file_records", [])}
    seeds = {(row["content_hash"], row["file_url"]) for row in records.values()}
    for file in candidates:
        if file.content_hash and file.file_url and file.file_name.startswith(data["prefix"] + "-"):
            seeds.add((file.content_hash, file.file_url))
    allowed = {("Item", name) for name in data["items"]} | {
        ("Jifeng Flow Order", name) for name in data["flows"]}
    known = {file.name for file in candidates if file.name}
    for digest, url in seeds:
        names = frappe.get_all("File", filters={"content_hash": digest, "file_url": url}, pluck="name")
        for name in names:
            file = frappe.get_doc("File", name)
            if not file.file_name.startswith(data["prefix"] + "-") or (
                file.attached_to_doctype, file.attached_to_name
            ) not in allowed:
                continue
            assert file.is_private and file.file_url.startswith("/private/files/" + data["prefix"] + "-")
            if name not in known:
                candidates.append(file)
                known.add(name)
            if name not in data["files"]:
                data["files"].append(name)
            record = records.setdefault(name, {"name": name, "native_clone": True})
            record.update({"file_name": file.file_name, "file_url": file.file_url,
                           "physical_path": file.get_full_path(), "content_hash": file.content_hash,
                           "attached_to_doctype": file.attached_to_doctype, "attached_to_name": file.attached_to_name})
    data["file_records"] = list(records.values())
    _journal(data)
    if objects is not None:
        objects[:] = candidates
    return candidates


def _remove_files(data, objects=None):
    """Use native File deletion; a failed/missing-record physical cleanup is an error."""
    errors = []
    candidates = _collect_files(data, objects)
    records = {row["name"]: row for row in data.get("file_records", [])}
    for file in reversed(candidates):
        try:
            if file.file_name and not file.file_name.startswith(data["prefix"] + "-"):
                raise AssertionError("拒绝清理其他附件")
            if file.name and frappe.db.exists("File", file.name):
                file.delete(ignore_permissions=True)
        except Exception as exc:
            errors.append(exc)
    # Several File records may share one blob. Check only after every tracked
    # reference has been removed through the native lifecycle.
    paths = set()
    for name, row in records.items():
        try:
            expected = Path(frappe.get_site_path("private", "files", row["file_url"].rsplit("/", 1)[-1])).resolve()
            if Path(row["physical_path"]).resolve() != expected or not row["file_url"].startswith(
                "/private/files/" + data["prefix"] + "-"):
                raise AssertionError("QA文件路径不属于当前夹具")
            if frappe.db.exists("File", name):
                raise AssertionError("原生File.delete后QA记录仍存在: " + name)
            paths.add(expected)
        except Exception as exc:
            errors.append(exc)
    for file in candidates:
        try:
            if file.name and frappe.db.exists("File", file.name):
                raise AssertionError("原生File.delete后QA记录仍存在: " + file.name)
            if file.file_url:
                paths.add(Path(file.get_full_path()).resolve())
        except Exception as exc:
            errors.append(exc)
    for path in paths:
        if path.exists():
            errors.append(AssertionError("删除所有QA附件引用后实物仍存在，需核对后处理: " + str(path)))
    if errors:
        raise ExceptionGroup("QA私有图片清理失败，不能视作已清理", errors)


def create(company, item_group, stock_uom):
    frappe.only_for("System Manager")
    frappe.get_doc("Company", company).check_permission("read")
    warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name")
    if not warehouse:
        frappe.throw("测试公司需要启用的明细仓库")
    from frappe.utils.file_manager import get_content_hash

    from jifeng_erp.production import department_progress
    from jifeng_erp.production_picture import production_picture

    suffix = frappe.generate_hash(length=12)
    prefix = "JF-DEPT-BROWSER-" + suffix
    data = {"fixture_type": "department_picture_browser", "fixture_version": 1, "prefix": prefix,
            "company": company, "item_group": item_group, "stock_uom": stock_uom,
            "created_at": now(), "phase": "creating", "cleaned": False,
            "file_records": [], "image_content": "唯一QA合成蓝橙棋盘PNG，不是业务商品图片",
            **{kind: [] for kind in KINDS}}
    original_user, files = frappe.session.user, []
    settings = frappe.get_single("Manufacturing Settings")
    data["original_disable_capacity_planning"] = settings.disable_capacity_planning
    data["settings_restored"] = False
    _journal(data)
    point = "jf_browser_" + suffix
    _begin_rollback_only(point)
    try:
        settings.disable_capacity_planning = 1
        settings.save()
        frappe.clear_document_cache("Manufacturing Settings", "Manufacturing Settings")
        departments = [_track(data, "departments", frappe.get_doc({
            "doctype": "Department", "department_name": prefix + f"-D{idx}",
            "company": company, "is_group": 0,
        }).insert()) for idx in (1, 2)]
        stations = [_track(data, "workstations", frappe.get_doc({
            "doctype": "Workstation", "workstation_name": prefix + f"-WS{idx}",
            "production_capacity": 1, "jf_department": department.name,
        }).insert()) for idx, department in enumerate(departments, 1)]
        operations = [_track(data, "operations", frappe.get_doc({
            "doctype": "Operation", "name": prefix + f"-OP{idx}",
            "workstation": station.name, "jf_department": department.name,
        }).insert()) for idx, (station, department) in enumerate(zip(stations, departments), 1)]
        raw, finished = [_track(data, "items", frappe.get_doc({
            "doctype": "Item", "item_code": prefix + "-" + kind,
            "item_name": "QA部门图片验收（非业务商品） " + kind,
            "item_group": item_group, "stock_uom": stock_uom,
        }).insert()) for kind in ("RAW", "FG")]
        payload = _qa_png(suffix + "-DEPARTMENT-BROWSER")
        digest = get_content_hash(payload)
        assert not frappe.db.exists("File", {"content_hash": digest}), "QA图片不能复用既有上传"
        image = frappe.get_doc({"doctype": "File", "file_name": prefix + "-QA-IMAGE.png", "content": payload,
                                "is_private": 1, "attached_to_doctype": "Item", "attached_to_name": finished.name})
        files.append(image)
        image.insert()
        _track(data, "files", image)
        assert image.content_hash == digest and image.file_name.startswith(prefix + "-")
        assert image.exists_on_disk() and image.get_content() == payload
        data["file_records"].append({"name": image.name, "file_name": image.file_name, "file_url": image.file_url,
                                     "physical_path": image.get_full_path(), "content_hash": image.content_hash,
                                     "native_clone": False, "attached_to_doctype": "Item", "attached_to_name": finished.name})
        _journal(data)
        finished.image = image.file_url
        finished.save()
        _collect_files(data, files)
        bom = _track(data, "boms", frappe.get_doc({
            "doctype": "BOM", "company": company, "item": finished.name, "quantity": 1,
            "with_operations": 1, "transfer_material_against": "Work Order",
            "items": [{"item_code": raw.name, "qty": 1, "rate": 10}],
            "operations": [{"operation": operation.name, "workstation": station.name, "time_in_mins": 1}
                           for operation, station in zip(operations, stations)],
        }).insert())
        bom.submit()
        flow = _track(data, "flows", frappe.get_doc({
            "doctype": "Jifeng Flow Order", "company": company, "item_code": finished.name,
            "planned_qty": 5, "bom": bom.name,
        }).insert())
        _collect_files(data, files)
        work = _track(data, "work_orders", frappe.get_doc("Work Order", flow.make_work_order()))
        work.source_warehouse, work.fg_warehouse, work.skip_transfer = warehouse, warehouse, 1
        work.planned_start_date = add_days(today(), -4) + " 08:00:00"
        work.save()
        work.submit()
        cards = [frappe.get_doc("Job Card", name) for name in frappe.get_all(
            "Job Card", filters={"work_order": work.name}, pluck="name", order_by="operation_row_id asc")]
        assert len(cards) == 2
        for idx, card in enumerate(cards):
            _track(data, "job_cards", card)
            card.expected_start_date = add_days(today(), -4) + " 08:00:00"
            card.expected_end_date = add_days(today(), -4) + " 09:00:00"
            card.pending_qty = 0 if idx == 0 else 4
            card.append("time_logs", {"from_time": today() + f" {9 + idx}:00:00",
                                      "to_time": today() + f" {9 + idx}:30:00", "completed_qty": 5 if idx == 0 else 1})
            card.save()
            card.submit()
            assert card.process_loss_qty == 0 and card.jf_department == departments[idx].name
        data["filters"] = {"company": company, "from_date": today(), "to_date": today(), "flow_order": flow.name}
        summary = department_progress(data["filters"])[1]
        assert sorted(row["approved_percent"] for row in summary) == [20, 100]
        overdue = department_progress({**data["filters"], "only_overdue": 1})[1]
        assert len(overdue) == 1 and overdue[0]["remaining_qty"] == 4 and overdue[0]["overdue_count"] == 1
        picture = production_picture(data["filters"])[1]
        _collect_files(data, files)
        assert len(picture) == 1
        picture_url = urlsplit(picture[0]["image_url"])
        fids = parse_qs(picture_url.query).get("fid", [])
        assert picture_url.path == image.file_url and len(fids) == 1 and fids[0] in data["files"], (picture, data["files"])
        assert not frappe.db.exists("Stock Ledger Entry", {"item_code": ["in", data["items"]]})
        settings.reload()
        settings.disable_capacity_planning = data["original_disable_capacity_planning"]
        settings.save()
        frappe.clear_document_cache("Manufacturing Settings", "Manufacturing Settings")
        data["settings_restored"] = True
        data["expected"] = {"department_percents": [100, 20], "overdue_remaining_qty": 4,
                            "overdue_days": 4, "produced_qty": 0, "image_url": picture[0]["image_url"]}
        data["phase"] = "commit_pending"
        _journal(data)
    except Exception as original_error:
        errors = [original_error]
        try:
            frappe.set_user("Administrator")
            _collect_files(data, files)
            _remove_files(data, files)
        except Exception as exc:
            errors.append(exc)
        try:
            _end_rollback_only(point)
            data["settings_restored"] = True
        except Exception as exc:
            errors.append(exc)
        finally:
            frappe.set_user(original_user)
            frappe.clear_document_cache("Manufacturing Settings", "Manufacturing Settings")
        data["phase"] = "create_failed"
        data["cleanup_errors"] = [str(error) for error in errors[1:]]
        _journal(data)
        original_error.add_note("QA夹具记录: " + data["artifact_path"])
        if len(errors) > 1:
            raise ExceptionGroup("部门图片QA创建或清理失败", errors)
        raise
    frappe.db._disable_transaction_control -= 1
    try:
        frappe.db.commit()
    except Exception as exc:
        data["phase"] = "commit_unknown"
        _journal(data)
        exc.add_note("提交结果待核对，先检查QA记录，不要重复创建: " + data["artifact_path"])
        raise
    data["phase"] = "ready"
    _journal(data)
    return data


def _validate_objects(data):
    prefix, company = data["prefix"], data["company"]
    docs = {kind: [frappe.get_doc(doctype, name) for name in data[kind] if frappe.db.exists(doctype, name)]
            for kind, doctype in KINDS.items()}
    for item in docs["items"]:
        assert item.item_code in (prefix + "-RAW", prefix + "-FG") and item.stock_uom == data["stock_uom"]
    for department in docs["departments"]:
        assert department.department_name in (prefix + "-D1", prefix + "-D2") and department.company == company
    for station in docs["workstations"]:
        assert station.workstation_name in (prefix + "-WS1", prefix + "-WS2") and station.jf_department in data["departments"]
    for operation in docs["operations"]:
        assert operation.name in (prefix + "-OP1", prefix + "-OP2") and operation.workstation in data["workstations"]
    for bom in docs["boms"]:
        assert bom.company == company and bom.item in data["items"] and len(bom.items) == 1
        assert bom.items[0].item_code in data["items"] and {row.operation for row in bom.operations} == set(data["operations"])
    for flow in docs["flows"]:
        assert flow.company == company and flow.item_code in data["items"] and flow.bom in data["boms"]
        assert not flow.sales_order and not flow.external_order and not flow.material_request and not flow.cost_preview
        assert not flow.work_order or flow.work_order in data["work_orders"]
    for work in docs["work_orders"]:
        assert work.company == company and work.production_item in data["items"] and work.bom_no in data["boms"]
        assert work.jf_flow_order in data["flows"] and flt(work.qty) == 5 and not work.production_plan and not work.sales_order
        assert {row.operation for row in work.operations} == set(data["operations"])
        assert set(frappe.get_all("Job Card", filters={"work_order": work.name}, pluck="name")) == set(data["job_cards"])
    for card in docs["job_cards"]:
        assert card.work_order in data["work_orders"] and card.operation in data["operations"] and card.company == company
        assert card.jf_flow_order in data["flows"] and card.jf_department in data["departments"]
    for file in docs["files"]:
        meta = next(row for row in data["file_records"] if row["name"] == file.name)
        assert file.file_name.startswith(prefix + "-") and file.content_hash == meta["content_hash"] and file.is_private
        assert file.file_url == meta["file_url"] and (file.attached_to_doctype, file.attached_to_name) in (
            {("Item", name) for name in data["items"]} | {("Jifeng Flow Order", name) for name in data["flows"]})
    assert not frappe.db.exists("Stock Ledger Entry", {"item_code": ["in", data["items"]]})
    assert not frappe.db.exists("Stock Entry", {"work_order": ["in", data["work_orders"]]})
    return docs


def cleanup(data):
    frappe.only_for("System Manager")
    if isinstance(data, str):
        data = json.loads(data)
    _journal(data)  # Validate the fixture identity and its private journal path.
    if not data.get("settings_restored"):
        frappe.throw("夹具设置恢复未确认，请先核对原设置，不能覆盖当前站点设置")
    original_user = frappe.session.user
    frappe.set_user("Administrator")
    try:
        _collect_files(data)
        docs = _validate_objects(data)
        data["phase"] = "cleaning"
        _journal(data)
        point = "jf_browser_cleanup_" + data["prefix"].rsplit("-", 1)[-1]
        _begin_rollback_only(point)
        try:
            _remove_files(data)
            for card in reversed(docs["job_cards"]):
                card.reload()
                if card.docstatus == 1:
                    card.cancel()
                card.delete()
            for work in docs["work_orders"]:
                work.reload()
                if work.docstatus == 1:
                    work.cancel()
                # Remove only the exact fixture flow's back-link before native
                # Work Order deletion; the flow will then be deleted as well.
                for flow in docs["flows"]:
                    if flow.work_order == work.name:
                        flow.db_set("work_order", None)
                work.delete()
            for flow in docs["flows"]:
                flow.delete()
            for bom in docs["boms"]:
                bom.reload()
                if bom.docstatus == 1:
                    bom.cancel()
                bom.delete()
            for kind in ("operations", "workstations", "departments", "items"):
                for doc in reversed(docs[kind]):
                    doc.delete()
            assert not any(frappe.db.exists(doctype, name) for kind, doctype in KINDS.items() for name in data[kind])
            data["phase"] = "cleanup_commit_pending"
            _journal(data)
        except Exception as exc:
            errors = [exc]
            try:
                _end_rollback_only(point)
            except Exception as rollback_error:
                errors.append(rollback_error)
            data["phase"] = "cleanup_failed"
            data["cleanup_errors"] = [str(error) for error in errors]
            _journal(data)
            if len(errors) > 1:
                raise ExceptionGroup("QA夹具清理和回滚失败", errors)
            raise
        frappe.db._disable_transaction_control -= 1
        try:
            frappe.db.commit()
        except Exception as exc:
            data["phase"] = "cleanup_commit_unknown"
            _journal(data)
            exc.add_note("清理提交结果待核对: " + data["artifact_path"])
            raise
        data["phase"], data["cleaned"] = "cleaned", True
        data["physical_files_cleaned"] = True
        _journal(data)
        return data
    finally:
        frappe.clear_document_cache("Manufacturing Settings", "Manufacturing Settings")
        frappe.set_user(original_user)
