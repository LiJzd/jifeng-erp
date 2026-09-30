"""Journaled native subcontract fixtures for an isolated site's browser acceptance.

Creation deliberately commits synthetic transactions. Cleanup uses native cancel
and delete only. Immutable ledger links can require retaining cancelled documents;
the private journal records that outcome without pretending that history was erased.
"""

import json
import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import frappe
from frappe.utils import add_days, flt, now, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only


KINDS = {"items": "Item", "suppliers": "Supplier", "warehouses": "Warehouse", "boms": "BOM",
         "flows": "Jifeng Flow Order", "purchase_orders": "Purchase Order",
         "subcontracting_orders": "Subcontracting Order", "stock_entries": "Stock Entry",
         "receipts": "Subcontracting Receipt"}

PARENT_FIELDS = {
    "Item": ("item_code", "item_group", "stock_uom", "is_stock_item", "is_sub_contracted_item"),
    "Supplier": ("supplier_name", "supplier_type", "supplier_group"),
    "Warehouse": ("warehouse_name", "company", "is_group"),
    "BOM": ("company", "item", "quantity"),
    "Jifeng Flow Order": ("company", "item_code", "planned_qty", "bom"),
    "Purchase Order": ("company", "supplier", "is_subcontracted"),
    "Subcontracting Order": ("company", "supplier", "purchase_order", "supplier_warehouse"),
    "Stock Entry": ("company", "purpose", "subcontracting_order"),
    "Subcontracting Receipt": ("company", "supplier", "is_return", "return_against", "supplier_warehouse"),
}

CHILD_FIELDS = {
    "BOM": ("item_code", "qty"),
    "Jifeng Flow Order": ("item_code", "uom", "qty_per_unit"),
    "Purchase Order": ("item_code", "qty", "fg_item", "fg_item_qty"),
    "Subcontracting Order": ("item_code", "qty", "stock_uom", "conversion_factor", "bom", "jf_flow_order"),
    "Stock Entry": ("item_code", "qty", "stock_uom", "s_warehouse", "t_warehouse",
                    "sco_rm_detail", "subcontracted_item", "jf_flow_order"),
    "Subcontracting Receipt": ("item_code", "qty", "rejected_qty", "process_loss_qty", "stock_uom",
                               "conversion_factor", "warehouse", "rejected_warehouse", "jf_flow_order",
                               "subcontracting_order", "subcontracting_order_item", "subcontracting_receipt_item"),
}

WAREHOUSE_HISTORY_GUARD = "Warehouse can not be deleted as stock ledger entry exists for this warehouse."


def _path(data):
    match = re.fullmatch(r"JF-SUB-BROWSER-([a-f0-9]{12})", data.get("prefix", ""))
    if not match or data.get("fixture_type") != "subcontract_browser":
        raise ValueError("不是受支持的委外QA夹具")
    root = Path(frappe.get_site_path("private", "jifeng_qa")).resolve()
    path = root / ("subcontract-browser-" + match[1] + ".json")
    if data.get("artifact_path") and Path(data["artifact_path"]).resolve() != path:
        raise ValueError("委外QA记录路径与夹具身份不一致")
    root.mkdir(parents=True, exist_ok=True)
    return path


def _journal(data):
    path = _path(data)
    data["artifact_path"], data["updated_at"] = str(path), now()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _snapshot(doc):
    result = {field: doc.get(field) for field in PARENT_FIELDS[doc.doctype]}
    if doc.doctype in CHILD_FIELDS:
        table = "materials" if doc.doctype == "Jifeng Flow Order" else "items"
        result[table] = [{"name": row.name, **{field: row.get(field) for field in CHILD_FIELDS[doc.doctype]}}
                         for row in doc.get(table) or []]
    return json.loads(json.dumps(result, default=str))


def _track(data, kind, doc):
    data[kind].append(doc.name)
    data["records"].append({"doctype": doc.doctype, "name": doc.name, "identity": _snapshot(doc)})
    _journal(data)
    return doc


def _refresh_records(data):
    for record in data["records"]:
        record["identity"] = _snapshot(frappe.get_doc(record["doctype"], record["name"]))
    _journal(data)


def _commit(data, phase):
    data["phase"] = phase + "_commit_pending"
    _journal(data)
    frappe.db._disable_transaction_control -= 1
    try:
        frappe.db.commit()
    except Exception as exc:
        data["phase"] = phase + "_commit_unknown"
        _journal(data)
        exc.add_note("提交结果待核对，先检查QA记录，不要重复创建或取消: " + data["artifact_path"])
        raise


def create(company, item_group, stock_uom):
    frappe.only_for("System Manager")
    frappe.get_doc("Company", company).check_permission("read")
    from erpnext.buying.doctype.purchase_order.purchase_order import make_subcontracting_order
    from erpnext.controllers.subcontracting_controller import make_rm_stock_entry
    from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import make_subcontracting_receipt
    from erpnext.subcontracting.doctype.subcontracting_receipt.subcontracting_receipt import (
        make_subcontract_return, make_subcontract_return_against_rejected_warehouse,
    )
    from jifeng_erp.subcontracting import subcontracting_progress, subcontracting_receipt_ledger

    supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
    if not supplier_group:
        frappe.throw("测试站点需要供应商分组")
    suffix = frappe.generate_hash(length=12)
    data = {"fixture_type": "subcontract_browser", "fixture_version": 1,
            "prefix": "JF-SUB-BROWSER-" + suffix, "company": company,
            "item_group": item_group, "stock_uom": stock_uom, "created_at": now(),
            "phase": "creating", "cleaned": False, "business_cancelled": False,
            "records": [], **{kind: [] for kind in KINDS}}
    prefix, point = data["prefix"], "jf_sub_browser_" + suffix
    settings = frappe.get_single("Buying Settings")
    data["original_auto_create_purchase_receipt"] = settings.auto_create_purchase_receipt
    data["settings_restored"] = False
    _journal(data)
    _begin_rollback_only(point)
    try:
        settings.auto_create_purchase_receipt = 0
        settings.save()
        frappe.clear_document_cache("Buying Settings", "Buying Settings")
        raw, finished, service = [_track(data, "items", frappe.get_doc({
            "doctype": "Item", "item_code": prefix + "-" + kind,
            "item_name": "QA委外报表验收（非业务商品） " + kind,
            "item_group": item_group, "stock_uom": stock_uom,
            "is_stock_item": int(kind != "SERVICE"), "is_sub_contracted_item": int(kind == "FG"),
        }).insert()) for kind in ("RAW", "FG", "SERVICE")]
        assert not frappe.db.exists("Stock Ledger Entry", {"item_code": ["in", data["items"]]})
        data["initial_stock_qty"] = 0
        warehouse, supplier_warehouse, rejected_warehouse = [_track(data, "warehouses", frappe.get_doc({
            "doctype": "Warehouse", "warehouse_name": prefix + "-" + kind, "company": company,
        }).insert()) for kind in ("SOURCE", "SUP", "REJ")]
        supplier = _track(data, "suppliers", frappe.get_doc({
            "doctype": "Supplier", "supplier_name": prefix + "-SUP", "supplier_type": "Company",
            "supplier_group": supplier_group,
        }).insert())
        bom = _track(data, "boms", frappe.get_doc({
            "doctype": "BOM", "company": company, "item": finished.name, "quantity": 1,
            "items": [{"item_code": raw.name, "qty": 1, "rate": 10}],
        }).insert())
        bom.submit()
        flow = _track(data, "flows", frappe.get_doc({
            "doctype": "Jifeng Flow Order", "company": company, "item_code": finished.name,
            "planned_qty": 10, "bom": bom.name,
            "materials": [{"item_code": raw.name, "uom": stock_uom, "qty_per_unit": 1, "unit_price": 10}],
        }).insert())
        stock = _track(data, "stock_entries", frappe.get_doc({
            "doctype": "Stock Entry", "company": company, "stock_entry_type": "Material Receipt",
            "items": [{"item_code": raw.name, "qty": 10, "basic_rate": 10, "t_warehouse": warehouse.name}],
        }).insert())
        stock.submit()
        purchase = _track(data, "purchase_orders", frappe.get_doc({
            "doctype": "Purchase Order", "company": company, "supplier": supplier.name,
            "is_subcontracted": 1, "transaction_date": add_days(today(), -2),
            "schedule_date": add_days(today(), -2), "set_warehouse": warehouse.name,
            "items": [{"item_code": service.name, "qty": 10, "rate": 20,
                       "fg_item": finished.name, "fg_item_qty": 10}],
        }).insert())
        purchase.submit()
        order = make_subcontracting_order(purchase.name)
        order.transaction_date, order.schedule_date = add_days(today(), -2), add_days(today(), -2)
        order.supplier_warehouse = supplier_warehouse.name
        order.items[0].bom, order.items[0].jf_flow_order = bom.name, flow.name
        order.items[0].schedule_date, order.items[0].expected_delivery_date = add_days(today(), -2), add_days(today(), -2)
        _track(data, "subcontracting_orders", order.insert())
        order.submit()
        supplied = order.supplied_items[0]
        transfer = _track(data, "stock_entries", frappe.get_doc(make_rm_stock_entry(order.name, [{
            "name": supplied.name, "main_item_code": finished.name, "rm_item_code": raw.name,
            "qty": 10, "stock_uom": stock_uom, "warehouse": warehouse.name,
        }])).insert())
        transfer.submit()
        assert transfer.items[0].sco_rm_detail == supplied.name and transfer.items[0].jf_flow_order == flow.name
        original_receipts = []
        for accepted, rejected, loss in ((2, 1, 1), (3, 1, 0)):
            receipt = make_subcontracting_receipt(order.name)
            receipt.items[0].qty, receipt.items[0].rejected_qty, receipt.items[0].process_loss_qty = accepted, rejected, loss
            receipt.items[0].rejected_warehouse = rejected_warehouse.name
            _track(data, "receipts", receipt.insert())
            receipt.submit()
            original_receipts.append(receipt)
        first = original_receipts[0]
        accepted_return = make_subcontract_return(first.name)
        accepted_return.items[0].qty, accepted_return.items[0].rejected_qty = -1, 0
        accepted_return.items[0].process_loss_qty = 0
        _track(data, "receipts", accepted_return.insert())
        accepted_return.submit()
        rejected_return = make_subcontract_return_against_rejected_warehouse(first.name)
        rejected_return.items[0].process_loss_qty = 0
        _track(data, "receipts", rejected_return.insert())
        rejected_return.submit()
        _refresh_records(data)
        data["order_filters"] = {"company": company, "from_date": add_days(today(), -2),
                                 "to_date": today(), "subcontracting_order": order.name}
        data["receipt_filters"] = {**data["order_filters"], "from_date": today()}
        progress = subcontracting_progress(data["order_filters"])[1]
        assert len(progress) == 1
        expected = {"planned_qty": 10, "accepted_qty": 4, "rejected_qty": 1, "loss_qty": 1,
                    "returned_qty": 2, "covered_qty": 6, "remaining_qty": 4, "pending_qty": 4,
                    "overdue_days": 2}
        assert all(flt(progress[0][field]) == value for field, value in expected.items()), progress
        ledger = subcontracting_receipt_ledger(data["receipt_filters"])[1]
        assert len(ledger) == 4 and sum(flt(row["covered_qty"]) for row in ledger) == 6
        data["expected"] = {"progress": expected, "receipt_rows": 4,
                            "receipt_movement": {row["receipt"]: {field: flt(row[field]) for field in
                                ("accepted_qty", "rejected_qty", "loss_qty", "covered_qty")} for row in ledger}}
        data["ledger_records"] = _ledger_rows(data)
        settings.reload()
        settings.auto_create_purchase_receipt = data["original_auto_create_purchase_receipt"]
        settings.save()
        frappe.clear_document_cache("Buying Settings", "Buying Settings")
        data["settings_restored"] = True
    except Exception as exc:
        _end_rollback_only(point)
        frappe.clear_document_cache("Buying Settings", "Buying Settings")
        data["phase"], data["settings_restored"] = "create_failed_rolled_back", True
        data["error"] = str(exc)
        _journal(data)
        exc.add_note("QA创建已回滚，记录: " + data["artifact_path"])
        raise
    _commit(data, "create")
    data["phase"] = "ready"
    _journal(data)
    return data


def _load(data):
    if isinstance(data, str):
        data = json.loads(data)
    canonical = json.loads(_path(data).read_text(encoding="utf-8"))
    for field in ("prefix", "company", "fixture_type", *KINDS):
        if data.get(field) != canonical.get(field):
            raise ValueError("调用参数与已保存QA身份或精确单据集合不一致")
    return canonical


def _validate(data):
    prefix, docs = data["prefix"], {}
    expected = {(record["doctype"], record["name"]): record["identity"] for record in data["records"]}
    assert set(expected) == {(doctype, name) for kind, doctype in KINDS.items() for name in data[kind]}
    for kind, doctype in KINDS.items():
        docs[kind] = []
        for name in data[kind]:
            if not frappe.db.exists(doctype, name):
                continue
            doc = frappe.get_doc(doctype, name)
            assert _snapshot(doc) == expected[(doctype, name)], "QA单据内容已变化，拒绝自动清理: " + name
            if doctype in ("BOM", "Jifeng Flow Order", "Purchase Order", "Subcontracting Order",
                            "Stock Entry", "Subcontracting Receipt", "Warehouse"):
                assert doc.company == data["company"]
            if doctype == "Item":
                assert doc.item_code in {prefix + "-" + kind for kind in ("RAW", "FG", "SERVICE")}
            if doctype in ("Supplier", "Warehouse"):
                assert doc.get("supplier_name" if doctype == "Supplier" else "warehouse_name").startswith(prefix + "-")
            if doctype == "Jifeng Flow Order":
                assert not any(doc.get(field) for field in
                               ("sales_order", "external_order", "material_request", "cost_preview", "work_order"))
            docs[kind].append(doc)
    for child_type, parenttype, kind in (
        ("Stock Entry Detail", "Stock Entry", "stock_entries"),
        ("Subcontracting Receipt Item", "Subcontracting Receipt", "receipts"),
        ("Subcontracting Order Item", "Subcontracting Order", "subcontracting_orders"),
        ("Purchase Order Item", "Purchase Order", "purchase_orders"),
    ):
        parents = set(frappe.get_all(child_type, filters={"item_code": ["in", data["items"]],
                                                        "parenttype": parenttype}, pluck="parent"))
        assert parents <= set(data[kind]), "QA商品被未追踪单据引用，保留夹具并人工核对"
    _ledger_rows(data)
    return docs


def _ledger_rows(data):
    owned = {("Stock Entry", name) for name in data["stock_entries"]} | {
        ("Subcontracting Receipt", name) for name in data["receipts"]}
    stock = frappe.get_all("Stock Ledger Entry", filters={"item_code": ["in", data["items"]]},
                           fields=["name", "company", "item_code", "warehouse", "voucher_type", "voucher_no",
                                   "actual_qty", "stock_value_difference", "is_cancelled"], limit_page_length=0)
    gl = frappe.get_all("GL Entry", filters={"voucher_no": ["in", [name for _, name in owned]]},
                        fields=["name", "company", "account", "voucher_type", "voucher_no", "debit", "credit",
                                "is_cancelled"], limit_page_length=0)
    assert all((row.voucher_type, row.voucher_no) in owned and row.company == data["company"]
               and row.warehouse in data["warehouses"] for row in stock), "QA物料出现未追踪库存凭证，不能清理"
    warehouse_rows = frappe.get_all("Stock Ledger Entry", filters={"warehouse": ["in", data["warehouses"]]},
                                    pluck="name", limit_page_length=0)
    assert set(warehouse_rows) == {row.name for row in stock}, "QA仓库出现其他物料库存历史，不能自动清理或停用"
    assert all((row.voucher_type, row.voucher_no) in owned and row.company == data["company"]
               for row in gl), "QA总账来源不属于精确夹具集合"
    return {"stock": [dict(row) for row in stock], "gl": [dict(row) for row in gl]}


def _restored(data):
    rows = _ledger_rows(data)
    stock_total, gl_total = defaultdict(Decimal), defaultdict(Decimal)
    for row in rows["stock"]:
        stock_total[(row["item_code"], row["warehouse"])] += Decimal(str(row["actual_qty"] or 0))
    for row in rows["gl"]:
        gl_total[row["account"]] += Decimal(str(row["debit"] or 0)) - Decimal(str(row["credit"] or 0))
    assert all(abs(value) < Decimal("0.000001") for value in stock_total.values()), "QA库存凭证尚未全部冲销"
    assert all(abs(value) < Decimal("0.000001") for value in gl_total.values()), "QA按账户总账净额尚未冲销"
    assert all(row["is_cancelled"] for row in rows["stock"]), "QA存在未取消的库存明细"
    zero_fields = ("actual_qty", "stock_value", "ordered_qty", "reserved_qty_for_sub_contract", "projected_qty")
    bins = frappe.get_all("Bin", filters={"item_code": ["in", data["items"]]},
                         fields=["item_code", "warehouse", *zero_fields], limit_page_length=0)
    assert all(abs(flt(row.get(field))) < 0.000001 for row in bins for field in zero_fields), \
        "QA库存数量、估值、订购或委外预留尚未恢复零余额"
    assert not any(frappe.db.get_value(doctype, name, "docstatus") == 1 for kind, doctype in KINDS.items()
                   if kind in ("receipts", "stock_entries", "subcontracting_orders", "purchase_orders", "boms")
                   for name in data[kind]), "QA提交单据未全部取消"
    data["stock_restored"], data["gl_restored"] = True, True
    data["cancelled_ledger_records"] = rows
    data["remaining_stock_bins"] = [dict(row) for row in bins]


def _is_history_guard(data, record):
    if record["error_type"] == "LinkExistsError":
        return True
    if (record["doctype"] != "Warehouse" or record["name"] not in data["warehouses"]
            or record["error_type"] != "ValidationError"
            or record["reason"] not in (WAREHOUSE_HISTORY_GUARD, frappe._(WAREHOUSE_HISTORY_GUARD))):
        return False
    # Warehouse.on_trash uses this exact guard whenever an SLE exists, including
    # cancelled history. _restored verifies every owned row and every net balance;
    # _ledger_rows also rejects any unrelated item history in these warehouses.
    return bool(frappe.db.exists("Stock Ledger Entry", {"warehouse": record["name"]}))


def _classify_history(data):
    _restored(data)
    retained = data.get("retained_records", [])
    data["cleanup_errors"] = [record for record in retained if not _is_history_guard(data, record)]
    data["remaining_rows"] = {kind: [name for name in data[kind] if frappe.db.exists(doctype, name)]
                              for kind, doctype in KINDS.items()}
    data["cleaned"] = not any(data["remaining_rows"].values())
    data["phase"] = "cleaned" if data["cleaned"] else (
        "cancelled_cleanup_incomplete" if data["cleanup_errors"] else "cancelled_history_retained")


def finalize_history(data):
    """Read verified cancelled history and correct its journal classification only."""
    frappe.only_for("System Manager")
    data = _load(data)
    if not data.get("business_cancelled") or not data.get("settings_restored"):
        frappe.throw("尚未确认原生取消和设置恢复，不能认定已留存历史")
    _validate(data)
    _classify_history(data)
    data["history_verified_at"] = now()
    _journal(data)
    return data


def disable_retained_masters(data):
    """Stop only the exact owned QA masters through their native disabled field."""
    frappe.only_for("System Manager")
    data = _load(data)
    if not data.get("business_cancelled") or not data.get("settings_restored"):
        frappe.throw("请先完成原生取消和设置恢复，再停用QA主档")
    original_user = frappe.session.user
    frappe.set_user("Administrator")
    try:
        docs = _validate(data)
        _classify_history(data)
        if data["cleanup_errors"]:
            frappe.throw("仍有未确认的清理异常，请核对后再停用QA主档")
        before = _ledger_rows(data)
        changes, added = data.setdefault("disabled_master_records", []), []
        point = "jf_sub_disable_" + data["prefix"].rsplit("-", 1)[-1]
        _begin_rollback_only(point)
        try:
            for kind in ("items", "suppliers", "warehouses"):
                for doc in docs[kind]:
                    field = frappe.get_meta(doc.doctype).get_field("disabled")
                    assert field and field.fieldtype == "Check", "QA主档没有受支持的原生停用字段"
                    previous = int(doc.get("disabled") or 0)
                    if previous:
                        continue
                    doc.disabled = 1
                    doc.save()
                    assert frappe.db.get_value(doc.doctype, doc.name, "disabled") == 1
                    added.append({"doctype": doc.doctype, "name": doc.name, "field": "disabled",
                                  "previous_value": previous, "value": 1, "changed_at": now()})
            assert before == _ledger_rows(data), "停用QA主档不能改变库存或总账历史"
            _restored(data)
        except Exception as exc:
            _end_rollback_only(point)
            data["disable_error"] = str(exc)
            data["disable_rolled_back_records"] = added
            _journal(data)
            raise
        changes.extend(added)
        _commit(data, "disable_masters")
        data["masters_disabled"] = True
        data["unsupported_master_actions"] = [{"doctype": "Jifeng Flow Order", "name": doc.name,
                                                "reason": "原生模型无disabled或业务取消状态，保留已核对的QA历史来源"}
                                               for doc in docs["flows"]]
        _classify_history(data)
        _journal(data)
        return data
    finally:
        frappe.set_user(original_user)


def finish_retained_reposts(data):
    """Finish only this cancelled QA fixture's native valuation jobs, with a journal."""
    frappe.only_for("System Manager")
    data = _load(data)
    if not data.get("business_cancelled") or not data.get("settings_restored"):
        frappe.throw("只能核对已撤销并恢复设置的委外QA夹具重算任务")
    from erpnext.stock.doctype.repost_item_valuation.repost_item_valuation import repost

    original_user = frappe.session.user
    frappe.set_user("Administrator")
    try:
        _validate(data)
        owned_sources = {(record["doctype"], record["name"]) for record in data["records"]}
        owned_items, owned_warehouses = set(data["items"]), set(data["warehouses"])
        jobs = frappe.get_all("Repost Item Valuation", filters={
            "company": data["company"], "docstatus": 1, "status": ["in", ["Queued", "In Progress"]],
        }, fields=["name", "based_on", "item_code", "warehouse", "voucher_type", "voucher_no", "status"],
            order_by="posting_date, posting_time, creation", limit_page_length=0)
        selected = [job for job in jobs if (
            (job.based_on == "Item and Warehouse" and job.item_code in owned_items and job.warehouse in owned_warehouses)
            or (job.based_on == "Transaction" and (job.voucher_type, job.voucher_no) in owned_sources))]
        # Unique QA masters must not have acquired unrelated business movements.
        history = frappe.get_all("Stock Ledger Entry", filters={"item_code": ["in", list(owned_items)]},
                                 fields=["voucher_type", "voucher_no", "warehouse"], limit_page_length=0)
        assert all((row.voucher_type, row.voucher_no) in owned_sources and row.warehouse in owned_warehouses for row in history)
        data["valuation_reposts"] = [{"name": job.name, "before_status": job.status} for job in selected]
        data["valuation_reposts_started_at"] = now()
        _journal(data)
        point = "jf_sub_reposts_" + data["prefix"].rsplit("-", 1)[-1]
        _begin_rollback_only(point)
        try:
            for job in selected:
                document = frappe.get_doc("Repost Item Valuation", job.name)
                if document.status in ("Queued", "In Progress"):
                    repost(document)
                status = frappe.db.get_value("Repost Item Valuation", job.name, "status")
                if status not in ("Completed", "Skipped"):
                    frappe.throw("委外QA库存重算未完成，请核对原生重算任务")
            _restored(data)
        except Exception as exc:
            _end_rollback_only(point)
            data["valuation_reposts_error"] = str(exc)
            _journal(data)
            raise
        _commit(data, "finish_reposts")
        for job in data["valuation_reposts"]:
            job["after_status"] = frappe.db.get_value("Repost Item Valuation", job["name"], "status")
        data["valuation_reposts_completed"] = True
        data["valuation_reposts_completed_at"] = now()
        _classify_history(data)
        _journal(data)
        return {"passed": True, "completed_jobs": len(selected), "stock_restored": data["stock_restored"],
                "gl_restored": data["gl_restored"], "journal": data["artifact_path"]}
    finally:
        frappe.set_user(original_user)


def cleanup(data):
    frappe.only_for("System Manager")
    data = _load(data)
    if not data.get("settings_restored"):
        frappe.throw("夹具设置恢复尚未确认，请先核对，不能覆盖当前站点设置")
    original_user = frappe.session.user
    frappe.set_user("Administrator")
    try:
        docs = _validate(data)
        suffix = data["prefix"].rsplit("-", 1)[-1]
        point = "jf_sub_cancel_" + suffix
        _begin_rollback_only(point)
        try:
            # Native cancellation restores receipt consumption first, then the
            # supplied material transfer, original stock and purchasing reserve.
            cancelled_types = {"Subcontracting Receipt", "Stock Entry", "Subcontracting Order", "Purchase Order", "BOM"}
            for record in reversed(data["records"]):
                if record["doctype"] not in cancelled_types or not frappe.db.exists(record["doctype"], record["name"]):
                    continue
                doc = frappe.get_doc(record["doctype"], record["name"])
                if doc.docstatus == 1:
                    doc.cancel()
            _restored(data)
        except Exception as exc:
            _end_rollback_only(point)
            data["phase"], data["cancel_error"] = "cancellation_failed_rolled_back", str(exc)
            _journal(data)
            raise
        _commit(data, "cancel")
        data["business_cancelled"] = True
        data["phase"] = "cancelled"
        _journal(data)

        # Each native deletion gets its own savepoint. Link restrictions retain
        # the exact cancelled history rather than rolling valid reversals back.
        retained = []
        for kind in ("receipts", "stock_entries", "subcontracting_orders", "purchase_orders",
                     "flows", "boms", "items", "warehouses", "suppliers"):
            for doc in reversed(docs[kind]):
                if not frappe.db.exists(doc.doctype, doc.name):
                    continue
                deletion_point = "jf_sub_delete"
                _begin_rollback_only(deletion_point)
                try:
                    frappe.get_doc(doc.doctype, doc.name).delete()
                except Exception as exc:
                    _end_rollback_only(deletion_point)
                    retained.append({"doctype": doc.doctype, "name": doc.name,
                                     "error_type": type(exc).__name__, "reason": str(exc)})
                else:
                    frappe.db._disable_transaction_control -= 1
        data["phase"] = "deletion_commit_pending"
        _journal(data)
        try:
            frappe.db.commit()
        except Exception as exc:
            data["phase"] = "deletion_commit_unknown"
            _journal(data)
            exc.add_note("原生删除提交结果待核对: " + data["artifact_path"])
            raise
        data["retained_records"] = retained
        _classify_history(data)
        _journal(data)
        return data
    finally:
        frappe.set_user(original_user)
