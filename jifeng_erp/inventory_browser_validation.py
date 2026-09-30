"""Journaled native inventory fixtures for browser acceptance on an isolated site.

Every business change uses a native document action. Cancellation retains its
immutable history; exact QA masters are disabled after ledger and Bin checks.
"""

import json
import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import frappe
from frappe.utils import cint, now, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _fixture


KINDS = {
    "uoms": "UOM", "items": "Item", "warehouse_types": "Warehouse Type",
    "warehouses": "Warehouse", "suppliers": "Supplier", "stock_entries": "Stock Entry",
    "material_requests": "Material Request", "purchase_orders": "Purchase Order",
    "purchase_receipts": "Purchase Receipt", "reconciliations": "Stock Reconciliation",
}
BUSINESS_TYPES = {"Stock Entry", "Material Request", "Purchase Order", "Purchase Receipt", "Stock Reconciliation"}
STOCK_TYPES = {"Stock Entry", "Purchase Receipt", "Stock Reconciliation"}
PARENT_FIELDS = {
    "UOM": ("uom_name", "must_be_whole_number"),
    "Item": ("item_code", "item_group", "stock_uom", "is_stock_item", "valuation_method"),
    "Warehouse Type": ("name",),
    "Warehouse": ("warehouse_name", "company", "is_group", "warehouse_type"),
    "Supplier": ("supplier_name", "supplier_type", "supplier_group"),
    "Stock Entry": ("company", "purpose", "posting_date", "posting_time", "is_return",
                    "is_opening", "add_to_transit", "outgoing_stock_entry", "work_order"),
    "Material Request": ("company", "material_request_type", "transaction_date", "schedule_date"),
    "Purchase Order": ("company", "supplier", "transaction_date", "schedule_date", "currency"),
    "Purchase Receipt": ("company", "supplier", "posting_date", "posting_time", "currency",
                         "conversion_rate", "is_return", "return_against"),
    "Stock Reconciliation": ("company", "purpose", "posting_date", "posting_time"),
}
CHILD_FIELDS = {
    "Item": ("uom", "conversion_factor"),
    "Stock Entry": ("item_code", "qty", "uom", "stock_uom", "conversion_factor", "transfer_qty",
                    "s_warehouse", "t_warehouse", "against_stock_entry", "ste_detail", "jf_flow_order"),
    "Material Request": ("item_code", "qty", "uom", "stock_uom", "conversion_factor", "stock_qty", "warehouse"),
    "Purchase Order": ("item_code", "qty", "uom", "stock_uom", "conversion_factor", "stock_qty", "rate",
                       "warehouse", "material_request", "material_request_item"),
    "Purchase Receipt": ("item_code", "qty", "uom", "stock_uom", "conversion_factor", "stock_qty", "rate",
                         "received_qty", "rejected_qty", "warehouse", "rejected_warehouse", "purchase_order",
                         "purchase_order_item", "purchase_receipt_item", "return_qty_from_rejected_warehouse"),
    # Current quantities, valuation amounts and differences are derived from
    # native reposting; identity uses only the counted quantities and rate.
    "Stock Reconciliation": ("item_code", "warehouse", "stock_uom", "qty", "valuation_rate"),
}
ZERO_BIN_FIELDS = (
    "actual_qty", "stock_value", "ordered_qty", "indented_qty", "planned_qty", "reserved_qty",
    "reserved_qty_for_production", "reserved_qty_for_sub_contract", "reserved_qty_for_production_plan",
    "reserved_stock", "projected_qty",
)


def _path(data):
    match = re.fullmatch(r"JF-INV-BROWSER-([a-f0-9]{12})", data.get("prefix", ""))
    if not match or data.get("fixture_type") != "inventory_browser":
        raise ValueError("不是受支持的库存采购验收夹具")
    root = Path(frappe.get_site_path("private", "jifeng_qa")).resolve()
    path = root / ("inventory-browser-" + match[1] + ".json")
    if data.get("artifact_path") and Path(data["artifact_path"]).resolve() != path:
        raise ValueError("库存验收日志路径与夹具身份不一致")
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
        table = "uoms" if doc.doctype == "Item" else "items"
        result[table] = [{"name": row.name, "parent": row.parent, "parenttype": row.parenttype,
                          **{field: row.get(field) for field in CHILD_FIELDS[doc.doctype]}}
                         for row in doc.get(table) or []]
    return json.loads(json.dumps(result, default=str))


def _track(data, doc):
    kind = next(kind for kind, doctype in KINDS.items() if doctype == doc.doctype)
    assert doc.name not in data[kind], "重复的验收来源凭证"
    data[kind].append(doc.name)
    data["records"].append({"doctype": doc.doctype, "name": doc.name, "creation": str(doc.creation),
                            "docstatus": cint(doc.docstatus), "identity": _snapshot(doc)})
    _journal(data)


def _begin(point):
    # A persistent fixture must never silently commit inside another caller's
    # rollback-only diagnostic transaction.
    if getattr(frappe.db, "_disable_transaction_control", 0):
        frappe.throw("持久验收夹具不能在其他禁止提交的事务中创建或撤销")
    _begin_rollback_only(point)


def _commit(data, phase):
    data["phase"] = phase + "_commit_pending"
    _journal(data)
    assert frappe.db._disable_transaction_control == 1, "验收事务提交屏障状态不一致"
    frappe.db._disable_transaction_control -= 1
    try:
        frappe.db.commit()
    except Exception as exc:
        data["phase"] = phase + "_commit_unknown"
        _journal(data)
        exc.add_note("提交结果待核对，先检查私有日志及原生单据状态: " + data["artifact_path"])
        raise


def _load(data):
    if isinstance(data, str):
        data = json.loads(data)
    canonical = json.loads(_path(data).read_text(encoding="utf-8"))
    for field in ("prefix", "company", "fixture_type", "source_suffix", *KINDS):
        if data.get(field) != canonical.get(field):
            raise ValueError("调用参数与私有日志中的夹具身份或精确来源集合不一致")
    return canonical


def _owned_sources(data):
    return {(record["doctype"], record["name"]) for record in data["records"]
            if record["doctype"] in STOCK_TYPES}


def _ledger_rows(data):
    owned = _owned_sources(data)
    stock = frappe.get_all("Stock Ledger Entry", filters={"item_code": ["in", data["items"]]},
        fields=["name", "company", "item_code", "stock_uom", "warehouse", "voucher_type", "voucher_no",
                "voucher_detail_no", "posting_date", "posting_time", "actual_qty", "qty_after_transaction",
                "stock_value", "stock_value_difference", "is_adjustment_entry", "is_cancelled"],
        order_by="name", limit_page_length=0)
    details = {(record["doctype"], record["name"]): {row["name"]: row for row in record["identity"].get("items", [])}
               for record in data["records"] if record["doctype"] in STOCK_TYPES}
    for row in stock:
        key = (row.voucher_type, row.voucher_no)
        child = details.get(key, {}).get(row.voucher_detail_no)
        assert key in owned and child and row.company == data["company"], "验收商品出现日志外库存凭证，拒绝自动撤销"
        assert row.item_code == child["item_code"] and row.stock_uom == child["stock_uom"], "验收账簿商品或库存单位与来源不一致"
        allowed = ({child.get("s_warehouse"), child.get("t_warehouse")} if row.voucher_type == "Stock Entry"
                   else {child.get("warehouse"), child.get("rejected_warehouse")})
        assert row.warehouse in allowed and row.warehouse in data["warehouses"], "验收库存流水出现日志外仓库"
    warehouse_rows = frappe.get_all("Stock Ledger Entry", filters={"warehouse": ["in", data["warehouses"]]},
                                    pluck="name", limit_page_length=0)
    assert set(warehouse_rows) == {row.name for row in stock}, "验收仓库存在其他商品历史，拒绝撤销或停用"
    gl = frappe.get_all("GL Entry", filters={"voucher_no": ["in", sorted({name for _, name in owned})]},
        fields=["name", "company", "account", "voucher_type", "voucher_no", "debit", "credit", "is_cancelled"],
        order_by="name", limit_page_length=0)
    assert all((row.voucher_type, row.voucher_no) in owned and row.company == data["company"] for row in gl), \
        "验收总账来源或公司与精确日志不一致"
    return {"stock": [dict(row) for row in stock], "gl": [dict(row) for row in gl]}


def _validate(data):
    expected = {(record["doctype"], record["name"]): record for record in data["records"]}
    assert len(expected) == len(data["records"]), "验收日志存在重复来源"
    assert set(expected) == {(doctype, name) for kind, doctype in KINDS.items() for name in data[kind]}, "验收日志来源集合不完整"
    docs = {}
    for kind, doctype in KINDS.items():
        docs[kind] = []
        for name in data[kind]:
            assert frappe.db.exists(doctype, name), "验收来源已被删除，拒绝自动撤销: " + name
            doc = frappe.get_doc(doctype, name)
            record = expected[(doctype, name)]
            assert str(doc.creation) == record["creation"] and _snapshot(doc) == record["identity"], \
                "验收来源身份或内容已变化，拒绝自动撤销: " + name
            allowed_status = {1, 2} if record["docstatus"] == 1 else {record["docstatus"]}
            assert cint(doc.docstatus) in allowed_status, "验收来源审核状态发生未经追踪的变化: " + name
            if doc.meta.has_field("company"):
                assert doc.company == data["company"], "验收来源公司不一致"
            docs[kind].append(doc)
    suffix = data["source_suffix"]
    assert set(data["items"]) == {"JF-INV-" + suffix, "JF-INV-HIDDEN-" + suffix}
    assert data["uoms"] == ["JF-INV-PAIR-" + suffix]
    assert {doc.warehouse_name for doc in docs["warehouses"]} == {f"JF-INV-{kind}-{suffix}" for kind in ("A", "B", "REJECTED", "TRANSIT")}
    assert {doc.supplier_name for doc in docs["suppliers"]} == {"JF-INV-SUP-" + suffix}
    for child_type, parenttype, kind in (
        ("Stock Entry Detail", "Stock Entry", "stock_entries"),
        ("Material Request Item", "Material Request", "material_requests"),
        ("Purchase Order Item", "Purchase Order", "purchase_orders"),
        ("Purchase Receipt Item", "Purchase Receipt", "purchase_receipts"),
        ("Stock Reconciliation Item", "Stock Reconciliation", "reconciliations"),
        ("Purchase Invoice Item", "Purchase Invoice", None), ("Sales Order Item", "Sales Order", None),
        ("Sales Invoice Item", "Sales Invoice", None), ("Delivery Note Item", "Delivery Note", None),
    ):
        parents = set(frappe.get_all(child_type, filters={"item_code": ["in", data["items"]],
                                  "parenttype": parenttype}, pluck="parent", limit_page_length=0))
        assert parents <= set(data[kind] if kind else []), "验收商品被日志外业务单据引用，保留并人工核对"
    conversions = frappe.get_all("UOM Conversion Detail", filters={"uom": ["in", data["uoms"]]},
                                 pluck="parent", limit_page_length=0)
    assert set(conversions) <= set(data["items"]), "验收换算单位被其他商品引用，不能自动停用"
    _validate_supplier_references(data)
    _ledger_rows(data)
    return docs


def _validate_supplier_references(data):
    """A QA supplier can be referenced independently of the fixture's Item."""
    for doctype, kind in (("Supplier Quotation", None), ("Purchase Order", "purchase_orders"),
                          ("Purchase Receipt", "purchase_receipts"), ("Purchase Invoice", None),
                          ("Subcontracting Order", None), ("Subcontracting Receipt", None)):
        if not frappe.db.exists("DocType", doctype) or not frappe.get_meta(doctype).has_field("supplier"):
            continue
        references = frappe.get_all(doctype, filters={"supplier": ["in", data["suppliers"]]},
                                    pluck="name", limit_page_length=0)
        assert set(references) <= set(data[kind] if kind else []), \
            "验收供应商被日志外业务凭证引用，不能自动撤销或停用"
    for doctype in ("Payment Entry", "Journal Entry Account"):
        if not frappe.db.exists("DocType", doctype):
            continue
        meta = frappe.get_meta(doctype)
        if not meta.has_field("party"):
            continue
        query = {"party": ["in", data["suppliers"]]}
        if meta.has_field("party_type"):
            query["party_type"] = "Supplier"
        references = frappe.get_all(doctype, filters=query, pluck="name", limit_page_length=0)
        assert not references, "验收供应商被日志外收付款或会计凭证引用，不能自动撤销或停用"


def _repost_jobs(data):
    owned = _owned_sources(data)
    jobs = frappe.get_all("Repost Item Valuation", filters={"company": data["company"], "docstatus": 1},
        fields=["name", "based_on", "item_code", "warehouse", "voucher_type", "voucher_no", "status"],
        order_by="posting_date, posting_time, creation", limit_page_length=0)
    return [job for job in jobs if (
        (job.based_on == "Item and Warehouse" and job.item_code in data["items"] and job.warehouse in data["warehouses"])
        or (job.based_on == "Transaction" and (job.voucher_type, job.voucher_no) in owned))]


def _finish_reposts(data, phase):
    """Execute only the exact fixture's jobs through native synchronous reposting."""
    from erpnext.stock.doctype.repost_item_valuation.repost_item_valuation import repost

    _ledger_rows(data)
    test, through = frappe.in_test, frappe.flags.through_repost_item_valuation
    max_writes = frappe.db.MAX_WRITES_PER_TRANSACTION
    seen, completed = {}, 0
    try:
        frappe.in_test = True
        while True:
            jobs = _repost_jobs(data)
            for job in jobs:
                seen.setdefault(job.name, {"name": job.name, "based_on": job.based_on,
                    "item_code": job.item_code, "warehouse": job.warehouse, "voucher_type": job.voucher_type,
                    "voucher_no": job.voucher_no, "before_status": job.status})
                assert job.status in ("Queued", "In Progress", "Completed", "Skipped"), "验收原生估价任务处于失败状态"
            pending = [job for job in jobs if job.status in ("Queued", "In Progress")]
            if not pending:
                break
            for job in pending:
                document = frappe.get_doc("Repost Item Valuation", job.name)
                if document.status in ("Queued", "In Progress"):
                    repost(document)
                    completed += 1
                status = frappe.db.get_value("Repost Item Valuation", job.name, "status")
                assert status in ("Completed", "Skipped"), "验收原生库存估价重算未完成"
    finally:
        frappe.in_test = test
        frappe.flags.through_repost_item_valuation = through
        frappe.db.MAX_WRITES_PER_TRANSACTION = max_writes
    for record in seen.values():
        record["after_status"] = frappe.db.get_value("Repost Item Valuation", record["name"], "status")
    log = {"phase": phase, "completed_at": now(), "jobs_seen": len(seen), "jobs_executed": completed,
           "records": list(seen.values())}
    data.setdefault("valuation_repost_log", []).append(log)
    _ledger_rows(data)
    _journal(data)
    return log


def _browser_contract(data, fixture):
    dates = {str(key): str(value) for key, value in fixture.dates.items()}
    warehouse_names = {kind: doc.name for kind, doc in fixture.warehouses.items()}
    common = {"company": data["company"], "from_date": dates["10"], "to_date": str(today()), "item_code": fixture.item.name}
    data["dates"], data["warehouse_names"] = dates, warehouse_names
    data["item_code"], data["hidden_item_code"] = fixture.item.name, fixture.hidden_item.name
    data["supplier"] = fixture.supplier.name
    data["report_filters"] = {
        "Jifeng Purchase Request Ledger": {**common, "only_pending": 0},
        "Jifeng Purchase Receipt Ledger": {**common, "supplier": fixture.supplier.name},
        "Jifeng Stock Entry Ledger": {**common, "mode": "transfer"},
        "Jifeng Historical Stock Balance": {**common, "from_date": dates["5"], "warehouse": warehouse_names["A"], "include_zero": 1},
        "Jifeng Stock Reconciliation Ledger": {**common, "warehouse": warehouse_names["A"], "only_difference": 1},
    }
    data["expected"] = {
        "purchase_request": {"rows": 1, "material_request": fixture.request.name,
                             "requested_qty": 10, "ordered_qty": 10, "remaining_qty": 0, "pending_qty": 0},
        "purchase_receipts": {"rows": 4, "accepted_qty_total": 6, "rejected_qty_total": 1, "physical_stock_qty_total": 7,
            "movements": {fixture.first.name: [4, 1, 5], fixture.second.name: [3, 1, 4],
                          fixture.accepted_return.name: [-1, 0, -1], fixture.rejected_return.name: [0, -1, -1]}},
        "stock_entry": {"receipt_rows": 1, "receipt_document_qty": 15, "receipt_movement_qty": 30,
                        "issue_rows": 1, "issue_net_qty": -2, "transfer_rows": 3,
                        "transfer_movement_qty": {fixture.transfer.name: 3, fixture.outward.name: 4, fixture.inward.name: 4},
                        "transfer_net_qty_each": 0, "transfer_net_value_each": 0,
                        "inward": fixture.inward.name, "outgoing_stock_entry": fixture.outward.name,
                        "source_detail": fixture.outward.items[0].name, "warehouse_b_transfer_rows": 2},
        "historical_balance": {"rows": 1, "opening_qty": 27, "opening_val": 270,
                               "in_qty": 11, "in_val": 176, "out_qty": 30, "out_val": 350,
                               "bal_qty": 8, "bal_val": 96, "val_rate": 12},
        "reconciliations": {"rows": 5, "movements": {
            fixture.gain.name: [27, 30, 3, 270, 300, 30], fixture.loss.name: [30, 25, -5, 300, 250, -50],
            fixture.revalue.name: [25, 25, 0, 250, 300, 50], fixture.zero.name: [25, 0, -25, 300, 0, -300],
            fixture.restored.name: [0, 8, 8, 0, 96, 96]}},
        "ending_stock": {"A": {"qty": 8, "value": 96}, "B": {"qty": 7, "value": 70},
                         "REJECTED": {"qty": 1, "value": 10}, "TRANSIT": {"qty": 0, "value": 0}},
        "draft_or_cancelled_excluded": [fixture.draft_receipt.name, fixture.canceled_return.name,
            fixture.canceled_receipt.name, fixture.draft_stock.name, fixture.canceled_reconciliation.name,
            fixture.draft_reconciliation.name],
    }
    data["extra_report_filters"] = {
        "stock_receipt": {**common, "mode": "receipt"}, "stock_issue": {**common, "mode": "issue"},
        "stock_transfer_b": {**common, "mode": "transfer", "warehouse": warehouse_names["B"]},
        "zero_transit": {**common, "from_date": dates["0"], "warehouse": warehouse_names["TRANSIT"], "include_zero": 1},
        "transit_before_receiving": {**common, "from_date": dates["7"], "to_date": dates["7"],
                                     "warehouse": warehouse_names["TRANSIT"], "include_zero": 1},
    }


def create(company, item_group, stock_uom):
    """Create and commit exact synthetic native fixtures with a private journal."""
    frappe.only_for("System Manager")
    for doctype, name in (("Company", company), ("Item Group", item_group), ("UOM", stock_uom)):
        frappe.get_doc(doctype, name).check_permission("read")
    suffix = frappe.generate_hash(length=12)
    data = {"fixture_type": "inventory_browser", "fixture_version": 1, "prefix": "JF-INV-BROWSER-" + suffix,
            "company": company, "item_group": item_group, "stock_uom": stock_uom,
            "created_at": now(), "created_by": frappe.session.user, "phase": "creating",
            "business_cancelled": False, "settings_restored": False, "masters_disabled": False,
            "records": [], **{kind: [] for kind in KINDS}}
    settings = frappe.get_single("Buying Settings")
    field = "set_valuation_rate_for_rejected_materials"
    data["original_buying_settings"] = {field: settings.get(field)}
    transit_exists = bool(frappe.db.exists("Warehouse Type", "Transit"))
    _journal(data)
    point = "jf_inventory_create_" + suffix
    _begin(point)
    try:
        fixture = _fixture(company, item_group, stock_uom)
        data["source_suffix"] = fixture.suffix
        # This order follows native dependencies. Reversal traverses it backwards.
        documents = [fixture.alt_uom, fixture.item, fixture.hidden_item]
        if not transit_exists:
            documents.append(frappe.get_doc("Warehouse Type", "Transit"))
        documents.extend(fixture.warehouses.values())
        documents.extend([fixture.supplier, fixture.initial, fixture.request, fixture.purchase,
            fixture.first, fixture.second, fixture.draft_receipt, fixture.accepted_return,
            fixture.rejected_return, fixture.canceled_return, fixture.canceled_receipt, fixture.draft_stock,
            fixture.issued, fixture.transfer, fixture.outward, fixture.inward, fixture.gain, fixture.loss,
            fixture.revalue, fixture.zero, fixture.restored, fixture.canceled_reconciliation,
            fixture.draft_reconciliation, fixture.hidden_stock])
        for document in documents:
            _track(data, frappe.get_doc(document.doctype, document.name))
        data["native_fixture_reposts_completed"] = fixture.native_reposts
        _validate(data)
        _finish_reposts(data, "create")
        # Reposting may synchronize current reconciliation values, never its
        # counted quantities, source identities or warehouses.
        _validate(data)
        _browser_contract(data, fixture)
        data["ledger_records"] = _ledger_rows(data)
        settings.reload()
        for name, value in data["original_buying_settings"].items():
            setattr(settings, name, value)
        settings.save()
        frappe.clear_document_cache("Buying Settings", "Buying Settings")
        assert all(settings.get(name) == value for name, value in data["original_buying_settings"].items()), "验收采购设置尚未恢复"
        data["settings_restored"] = True
    except Exception as exc:
        _end_rollback_only(point)
        frappe.clear_document_cache("Buying Settings", "Buying Settings")
        data["phase"], data["error"], data["settings_restored"] = "create_failed_rolled_back", str(exc), True
        _journal(data)
        exc.add_note("库存验收创建已回滚，私有日志: " + data["artifact_path"])
        raise
    _commit(data, "create")
    data["phase"] = "ready"
    _journal(data)
    return data


def _near_zero(value):
    return abs(Decimal(str(value or 0))) <= Decimal("0.000001")


def _restored(data):
    rows = _ledger_rows(data)
    assert all(cint(row["is_cancelled"]) for row in rows["stock"]), "验收仍有有效库存流水"
    assert all(cint(row["is_cancelled"]) for row in rows["gl"]), "验收仍有有效总账分录"
    ordinary_qty, stock_value, gl_value, voucher_gl_value = (defaultdict(Decimal) for _ in range(4))
    for row in rows["stock"]:
        key = (row["item_code"], row["warehouse"])
        if row["voucher_type"] != "Stock Reconciliation":
            ordinary_qty[key] += Decimal(str(row["actual_qty"] or 0))
        stock_value[key] += Decimal(str(row["stock_value_difference"] or 0))
    for row in rows["gl"]:
        value = Decimal(str(row["debit"] or 0)) - Decimal(str(row["credit"] or 0))
        gl_value[row["account"]] += value
        voucher_gl_value[(row["voucher_type"], row["voucher_no"], row["account"])] += value
    assert all(_near_zero(value) for value in ordinary_qty.values()), "验收普通入出库有未冲销的库存数量"
    # Native cancellation marks both original and reversing SLE as cancelled.
    # Its valuation replay excludes them; a reverse SLE can therefore have a
    # zero value difference rather than the original's negative difference.
    # Retained cancelled history is not a current stock value calculation.
    assert all(_near_zero(value) for value in gl_value.values()), "验收总账按账户净额尚未归零"
    assert all(_near_zero(value) for value in voucher_gl_value.values()), "验收某来源凭证的总账按账户尚未归零"
    bins = frappe.get_all("Bin", filters={"item_code": ["in", data["items"]]},
        fields=["item_code", "warehouse", *ZERO_BIN_FIELDS], order_by="item_code, warehouse", limit_page_length=0)
    assert all(row.warehouse in data["warehouses"] for row in bins), "验收商品存在日志外仓库余额"
    assert all(_near_zero(row.get(field)) for row in bins for field in ZERO_BIN_FIELDS), "验收库存、估值、订购、需求或预留余额尚未归零"
    assert not any(frappe.db.get_value(record["doctype"], record["name"], "docstatus") == 1
                   for record in data["records"] if record["doctype"] in BUSINESS_TYPES), "验收原生单据尚未全部撤销"
    data["stock_restored"], data["gl_restored"] = True, True
    data["remaining_stock_bins"] = [dict(row) for row in bins]
    data["cancelled_ledger_records"] = rows
    data["ledger_net_checks"] = {
        "ordinary_quantity_by_item_warehouse": [{"item_code": key[0], "warehouse": key[1], "net_qty": str(value)} for key, value in ordinary_qty.items()],
        "retained_cancelled_stock_value_difference_sum": [{"item_code": key[0], "warehouse": key[1], "diagnostic_sum": str(value)} for key, value in stock_value.items()],
        "gl_by_account": [{"account": key, "net_value": str(value)} for key, value in gl_value.items()],
        "gl_by_voucher_account": [{"voucher_type": key[0], "voucher_no": key[1], "account": key[2], "net_value": str(value)} for key, value in voucher_gl_value.items()],
        "stock_value_check": "全部SLE已取消，原生估价重算已完成，各仓Bin库存价值归零；取消历史价值差额不作当前余额累加",
        "reconciliation_quantity_check": "原生取消后无有效流水且各仓Bin归零，绝对盘点数量不作actual_qty累加",
    }


def cleanup(data):
    """Cancel only journal-owned native vouchers, finish reposts, then disable QA masters."""
    frappe.only_for("System Manager")
    data = _load(data)
    if not data.get("settings_restored") or data["phase"] == "create_failed_rolled_back":
        frappe.throw("夹具没有确认创建或设置恢复，不能自动撤销")
    try:
        docs = _validate(data)
    except Exception as exc:
        data["cleanup_refused_at"], data["cleanup_validation_error"] = now(), str(exc)
        _journal(data)
        raise
    suffix = data["prefix"].rsplit("-", 1)[-1]
    point = "jf_inventory_cancel_" + suffix
    _begin(point)
    try:
        changes = []
        for record in reversed(data["records"]):
            if record["doctype"] not in BUSINESS_TYPES:
                continue
            doc = frappe.get_doc(record["doctype"], record["name"])
            if doc.docstatus == 1:
                doc.cancel()
                changes.append({"doctype": doc.doctype, "name": doc.name, "cancelled_at": now()})
        _finish_reposts(data, "cancel")
        _restored(data)
        _validate(data)
    except Exception as exc:
        _end_rollback_only(point)
        data["phase"], data["cancel_error"] = "cancellation_failed_rolled_back", str(exc)
        _journal(data)
        raise
    data.setdefault("cancelled_document_log", []).extend(changes)
    _commit(data, "cancel")
    data["business_cancelled"], data["phase"] = True, "cancelled"
    _journal(data)

    point = "jf_inventory_disable_" + suffix
    _begin(point)
    try:
        _validate(data)
        before = _ledger_rows(data)
        changes = []
        for kind, field, value in (("items", "disabled", 1), ("suppliers", "disabled", 1),
                                  ("warehouses", "disabled", 1), ("uoms", "enabled", 0)):
            for source in docs[kind]:
                doc = frappe.get_doc(source.doctype, source.name)
                definition = doc.meta.get_field(field)
                assert definition and definition.fieldtype == "Check", "验收主档没有受支持的原生停用字段"
                previous = cint(doc.get(field))
                if previous == value:
                    continue
                setattr(doc, field, value)
                doc.save()
                assert cint(frappe.db.get_value(doc.doctype, doc.name, field)) == value
                changes.append({"doctype": doc.doctype, "name": doc.name, "field": field,
                                "previous_value": previous, "value": value, "changed_at": now()})
        assert before == _ledger_rows(data), "停用验收主档不能改变库存或总账历史"
        _restored(data)
    except Exception as exc:
        _end_rollback_only(point)
        data["phase"], data["disable_error"] = "cancelled_disable_failed_rolled_back", str(exc)
        _journal(data)
        raise
    data.setdefault("disabled_master_records", []).extend(changes)
    _commit(data, "disable_masters")
    data["masters_disabled"], data["phase"] = True, "cancelled_history_retained"
    data["retained_history_records"] = [{"doctype": record["doctype"], "name": record["name"],
        "docstatus": frappe.db.get_value(record["doctype"], record["name"], "docstatus")}
        for record in data["records"]]
    data["retained_shared_masters"] = [{"doctype": "Warehouse Type", "name": name,
        "reason": "原生Transit仓库类型没有停用字段，保留已追踪的原生定义"} for name in data["warehouse_types"]]
    data["cleaned"] = False
    data["cleanup_complete"] = True
    _journal(data)
    return data
