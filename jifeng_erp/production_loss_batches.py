"""Prove tracked materials from their exact Work Order WIP transfer history.

Every read is supplied by the caller. The costing Session owns permission and
read-change closure; stock writers supply current NOWAIT readers. This module
never selects, writes, creates bundles or overrides native valuation itself.
"""

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import json

import frappe
from frappe.utils import flt


SE = "Stock Entry"
SLE = "Stock Ledger Entry"
BUNDLE = "Serial and Batch Bundle"
ENTRY = "Serial and Batch Entry"
REPOST = "Repost Item Valuation"
PERIOD_CLOSE = "Period Closing Voucher"
STOCK_CLOSE = "Stock Closing Entry"
CLOSING_BALANCE = "Stock Closing Balance"
LOSS_WORK = "jf_production_loss_work_order"
MAX_ROWS = 10000
ZERO = Decimal(0)
IDENTITY = {"name", "docstatus", "modified"}
CHILD = IDENTITY | {"parent", "parenttype", "parentfield"}
PARENTS = {
    SE: IDENTITY | {"company", "purpose", "work_order", LOSS_WORK, "posting_date", "posting_time", "is_return"},
    SLE: IDENTITY | {"company", "item_code", "warehouse", "stock_uom", "voucher_type", "voucher_no",
        "voucher_detail_no", "actual_qty", "stock_value_difference", "serial_and_batch_bundle", "batch_no",
        "serial_no", "is_cancelled", "is_adjustment_entry", "posting_date", "posting_time", "creation",
        "posting_datetime", "stock_queue", "stock_value", "qty_after_transaction", "valuation_rate"},
    BUNDLE: IDENTITY | {"company", "item_code", "warehouse", "voucher_type", "voucher_no", "voucher_detail_no",
        "type_of_transaction", "is_cancelled", "has_batch_no", "has_serial_no", "total_qty", "total_amount",
        "posting_datetime"},
    "Batch": IDENTITY | {"item", "disabled", "use_batchwise_valuation"},
    "Item": {"name", "modified", "stock_uom", "is_stock_item", "has_batch_no", "has_serial_no",
        "use_serial_no_wise_valuation", "valuation_method"},
    "Company": {"name", "modified", "valuation_method"},
    "Stock Settings": {"name", "modified", "do_not_use_batchwise_valuation"},
    "Serial No": IDENTITY | {"item_code", "batch_no", "company", "warehouse", "status"},
    PERIOD_CLOSE: IDENTITY | {"company", "period_end_date"},
    STOCK_CLOSE: IDENTITY | {"company", "status", "from_date", "to_date", "creation"},
    CLOSING_BALANCE: IDENTITY | {"stock_closing_entry", "item_code", "warehouse", "batch_no",
        "actual_qty", "stock_value_difference", "inventory_dimension_key"},
    REPOST: IDENTITY | {"company", "status", "item_code", "warehouse", "voucher_type", "voucher_no",
        "based_on", "reposting_data_file", "items_to_be_repost"},
}
TABLES = {
    SE: (("items", "Stock Entry Detail", CHILD | {"idx", "item_code", "stock_uom", "qty", "uom", "conversion_factor",
        "transfer_qty", "s_warehouse", "t_warehouse", "serial_no", "batch_no", "serial_and_batch_bundle",
        "use_serial_batch_fields", "basic_rate", "basic_amount", "amount", "valuation_rate"}),),
    BUNDLE: (("entries", ENTRY, CHILD | {"item_code", "warehouse", "batch_no", "serial_no", "qty",
        "incoming_rate", "stock_value_difference", "is_cancelled", "voucher_type", "voucher_no",
        "voucher_detail_no", "type_of_transaction", "posting_datetime", "creation", "stock_queue", "idx"}),),
}


def _fail(message="批号材料的原工单转料、批次数量或库存价值不能完整核对，请先核对原单"):
    frappe.throw(message)


def _number(value):
    try:
        result = Decimal(str(value if value not in (None, "") else 0))
    except (ValueError, TypeError, InvalidOperation):
        _fail()
    if not result.is_finite() or isinstance(value, bool):
        _fail()
    return result


def _same(left, right, document, field):
    precision = document.precision(field)
    if precision is None:
        _fail("批号材料缺少原生库存数量或金额精度")
    return flt(left, precision) == flt(right, precision)


def _records(kind, filters, *, read, records):
    if not callable(records):
        _fail("批号材料必须使用完整库存来源读取接口")
    rows = records(kind, filters, PARENTS[kind])
    if len(rows) > MAX_ROWS or len({row.get("name") for row in rows}) != len(rows):
        _fail()
    result = []
    for row in rows:
        document = read(kind, row.get("name"))
        document.check_permission("read")
        if any(document.get(field) != value for field, value in row.items() if field in PARENTS[kind]):
            _fail("批号库存来源在本次读取期间已变化，请重新读取")
        result.append(document)
    return result


def _batch(name, item, *, read):
    if not isinstance(name, str) or not name.strip():
        _fail("批号材料缺少明确选择的原批号")
    batch = read("Batch", name)
    batch.check_permission("read")
    if not name or batch.name != name or batch.item != item:
        _fail("所选批号与损耗材料商品不一致")
    return batch


def _serials(value):
    if not isinstance(value, str):
        _fail("序号材料缺少准确原序号")
    names = [name.strip() for name in value.splitlines() if name.strip()]
    if not names or len(names) != len(set(names)) or len(names) > MAX_ROWS:
        _fail("序号材料存在空号或重复序号")
    return names


def _serial(name, item, company, batch, *, read, current_warehouse=None):
    if not isinstance(name, str) or not name.strip():
        _fail("序号材料缺少准确原序号")
    doc = read("Serial No", name)
    doc.check_permission("read")
    if (doc.name != name or doc.item_code != item or doc.company != company
            or (doc.get("batch_no") or None) != (batch or None)):
        _fail("原序号的商品、公司或批号与准确库存来源不一致")
    if current_warehouse and (doc.get("warehouse") != current_warehouse or doc.get("status") != "Active"):
        _fail("所选原序号当前不在本工单在制仓或不可出库")
    return doc


def _mirror(doc, lots):
    if doc.get("serial_no") and set(_serials(doc.serial_no)) != {row["serial_no"] for row in lots.values()}:
        _fail("库存明细旧序号字段与原生序号凭证不一致")
    if doc.get("batch_no") and {row["batch_no"] for row in lots.values()} != {doc.batch_no}:
        _fail("损耗明细所选批号与真实出库批号不一致")


def _stamp(doc):
    # ISO dates and native posting_time strings/timedeltas preserve ordering
    # after padding the clock fields (timedelta string may start with 0:).
    date = str(doc.get("posting_date") or "")
    clock = str(doc.get("posting_time") or "00:00:00")
    pieces = clock.split(":")
    if not date or len(pieces) != 3:
        _fail("批号库存来源缺少准确记账日期时间")
    return date, tuple(Decimal(part) for part in pieces)


def _datetime(value):
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        _fail("原生批次凭证缺少准确历史日期时间")


def _precedes(sle, cutoff, before_creation=None):
    if not cutoff:
        return True
    stamp = _stamp(sle)
    return stamp < cutoff or stamp == cutoff and (
        before_creation is None or _datetime(sle.get("creation")) < _datetime(before_creation))


def _lots(sle, *, read, records, historical=False):
    qty, value = _number(sle.actual_qty), _number(sle.stock_value_difference)
    if sle.docstatus != 1 or not qty or sle.get("is_adjustment_entry") or qty * value < 0:
        _fail("批号库存账方向、状态或价值层无法唯一核对")
    item = read("Item", sle.item_code)
    item.check_permission("read")
    serial, batched = bool(item.get("has_serial_no")), bool(item.get("has_batch_no"))
    if not (serial or batched) or sle.stock_uom != item.stock_uom:
        _fail("库存账追踪方式或单位与原材料不一致")
    name = sle.get("serial_and_batch_bundle")
    if not name:
        if serial:
            # Installed SerialNoValuation uses each exact Inward child rate.
            # Its separate deprecated-SLE fallback is not proved here.
            _fail("旧序号库存账缺少逐号原入向凭证，当前入口不能猜测原号损耗金额")
        batch = sle.get("batch_no") or None
        if batched:
            _batch(batch, sle.item_code, read=read)
        elif batch:
            _fail("未启用批号的材料不能混入批号库存账")
        if sle.get("serial_no"):
            _fail("未启用序号的材料不能混入序号库存账")
        return {batch: {"qty": qty, "value": value, "rate": value / qty,
                        "serial_no": None, "batch_no": batch}}
    bundle = read(BUNDLE, name)
    bundle.check_permission("read")
    allowed = {1, 2} if historical else {1}
    if (bundle.docstatus not in allowed or (not historical and bundle.get("is_cancelled"))
            or bundle.company != sle.company or bundle.item_code != sle.item_code or bundle.warehouse != sle.warehouse
            or bool(bundle.get("has_serial_no")) != serial or bool(bundle.get("has_batch_no")) != batched
            or bundle.voucher_type != sle.voucher_type or bundle.voucher_no != sle.voucher_no
            or bundle.voucher_detail_no != sle.voucher_detail_no
            or bundle.type_of_transaction != ("Inward" if qty > 0 else "Outward")):
        _fail("批号凭证的原单、原行、公司、仓库或方向不一致")
    fields = TABLES[BUNDLE][0][2]
    stored = records(ENTRY, {"parent": name}, fields, parenttype=BUNDLE)
    children = {row.name: row for row in bundle.get("entries") or []}
    if (not children or len(children) > MAX_ROWS or len(children) != len(bundle.get("entries") or [])
            or set(children) != {row.get("name") for row in stored} or len(stored) != len(children)):
        _fail("原批号凭证明细不完整，不能遗漏隐藏批次")
    result = {}
    for raw in stored:
        row = children[raw["name"]]
        if (any(row.get(field) != amount for field, amount in raw.items() if field in fields)
                or row.parent != name or row.parenttype != BUNDLE or row.parentfield != "entries"
                or row.docstatus != bundle.docstatus
                or row.item_code != sle.item_code or row.warehouse != sle.warehouse
                or row.get("voucher_type") != sle.voucher_type or row.get("voucher_no") != sle.voucher_no
                or row.get("voucher_detail_no") != sle.voucher_detail_no
                or row.get("type_of_transaction") != bundle.type_of_transaction
                or _stamp({"posting_date": _datetime(row.get("posting_datetime")).date(),
                           "posting_time": _datetime(row.get("posting_datetime")).time()}) != _stamp(sle)
                or (not historical and row.get("is_cancelled"))):
            _fail("原批号凭证明细的物料、数量或父凭证不一致")
        batch, number, quantity = row.get("batch_no") or None, row.get("serial_no") or None, _number(row.qty)
        key = ("serial", number) if serial else batch
        rate, amount = _number(row.get("incoming_rate")), _number(row.get("stock_value_difference"))
        if (not key or key in result or not quantity or quantity * qty <= 0 or rate < 0
                or (serial and (not number or abs(quantity) != 1)) or (not serial and number)
                or not _same(amount, quantity * rate, row, "stock_value_difference")):
            _fail("同批重复或存在无法准确区分的历史价层，请逐层核对原单")
        if batched:
            _batch(batch, sle.item_code, read=read)
        elif batch:
            _fail("未启用批号的材料不能混入批号凭证")
        if serial:
            _serial(number, sle.item_code, sle.company, batch, read=read)
        result[key] = {"qty": quantity, "value": amount, "rate": rate, "batch_no": batch, "serial_no": number}
    if (not _same(sum((row["qty"] for row in result.values()), ZERO), qty, sle, "actual_qty")
            or not _same(sum((row["value"] for row in result.values()), ZERO), value, sle, "stock_value_difference")
            or not _same(bundle.total_qty, qty, sle, "actual_qty")
            or not _same(bundle.total_amount, value, sle, "stock_value_difference")):
        _fail("逐批数量或价值与确切库存账不一致")
    _mirror(sle, result)
    return result


def _selected_lots(doc, row, item, *, read, records):
    """Validate a native-created draft bundle without trusting its valuation."""
    batch = row.get("batch_no") or None
    serial, batched = bool(item.get("has_serial_no")), bool(item.get("has_batch_no"))
    selected = {}
    if row.get("serial_and_batch_bundle"):
        bundle = read(BUNDLE, row.serial_and_batch_bundle)
        bundle.check_permission("read")
        if (bundle.docstatus != 0 or bundle.get("is_cancelled") or bundle.company != doc.company
                or bundle.item_code != item.name or bundle.warehouse != row.s_warehouse
                or bundle.voucher_type != SE or bundle.voucher_no != doc.name
                or bundle.voucher_detail_no != row.name or bundle.type_of_transaction != "Outward"
                or bool(bundle.get("has_serial_no")) != serial or bool(bundle.get("has_batch_no")) != batched):
            _fail("损耗批号凭证必须是本单、本行准确新建的原生出向草稿")
        fields = TABLES[BUNDLE][0][2]
        entries = records(ENTRY, {"parent": bundle.name}, fields, parenttype=BUNDLE)
        actual = bundle.get("entries") or []
        native_rows = {child.name: child for child in actual}
        if (not entries or len(entries) > MAX_ROWS or len(native_rows) != len(actual)
                or len(entries) != len(actual) or {child.get("name") for child in entries} != set(native_rows)):
            _fail("新出向批次或序号明细不完整")
        for entry in entries:
            native = native_rows[entry["name"]]
            number, chosen_batch = entry.get("serial_no") or None, entry.get("batch_no") or None
            key = ("serial", number) if serial else chosen_batch
            quantity = _number(entry.get("qty"))
            if (any(native.get(field) != value for field, value in entry.items() if field in fields)
                    or entry.get("parent") != bundle.name or entry.get("parenttype") != BUNDLE
                    or entry.get("parentfield") != "entries" or entry.get("docstatus") != 0
                    or entry.get("is_cancelled") or not key or key in selected or quantity >= 0
                    or (serial and (not number or quantity != -1)) or (not serial and number)
                    or entry.get("item_code") != item.name or entry.get("warehouse") != row.s_warehouse):
                _fail("新出向批号凭证与本次损耗选择不一致")
            selected[key] = {"qty": quantity, "serial_no": number, "batch_no": chosen_batch}
    elif serial:
        selected = {("serial", number): {"qty": Decimal(-1), "serial_no": number, "batch_no": batch}
                    for number in _serials(row.get("serial_no"))}
    else:
        selected = {batch: {"qty": -_number(row.transfer_qty), "serial_no": None, "batch_no": batch}}
    if (not serial and len(selected) != 1
            or not _same(-sum((lot["qty"] for lot in selected.values()), ZERO), row.transfer_qty, row, "transfer_qty")):
        _fail("损耗明细数量与所选批次或逐号数量不一致")
    _mirror(row, selected)
    for lot in selected.values():
        if batched:
            if _batch(lot["batch_no"], item.name, read=read).get("disabled"):
                _fail("所选原批号已停用，不能新增损耗出库")
        elif lot["batch_no"]:
            _fail("未启用批号的材料不能混入批号损耗")
        if serial:
            _serial(lot["serial_no"], item.name, doc.company, lot["batch_no"], read=read,
                    current_warehouse=row.s_warehouse)
    return selected


def _transfers(work, *, read, records):
    documents = _records(SE, {"company": work.company, "work_order": work.name, "docstatus": 1,
        "purpose": "Material Transfer for Manufacture"}, read=read, records=records)
    result = {}
    for doc in documents:
        if doc.company != work.company or doc.work_order != work.name or doc.docstatus != 1:
            _fail("批号转料凭证不属于准确原工单")
        for row in doc.get("items") or []:
            if row.get("t_warehouse") != work.wip_warehouse or row.get("s_warehouse") == work.wip_warehouse:
                continue
            if (row.parent != doc.name or row.parenttype != SE or row.parentfield != "items"
                    or _number(row.transfer_qty) <= 0 or _number(row.conversion_factor) <= 0
                    or not _same(_number(row.qty) * _number(row.conversion_factor), row.transfer_qty, row, "transfer_qty")):
                _fail()
            result[doc.name, row.name, row.item_code] = doc, row
    return result


def _reposting(work, items, transfers, *, read, records):
    jobs = _records(REPOST, {"company": work.company, "docstatus": 1,
        "status": ["not in", ["Completed", "Skipped", "Cancelled"]]}, read=read, records=records)
    for job in jobs:
        if ((job.get("item_code") in items and job.get("warehouse") == work.wip_warehouse)
                or (job.get("voucher_type") == SE and job.get("voucher_no") in {key[0] for key in transfers})
                or not job.get("item_code") or not job.get("warehouse")):
            _fail("相关批号库存正在重估或范围无法确认，请完成重估后再办理")


def _add_origin(origin, key, values, doc, row, sle):
    source = {"stock_entry": doc.name, "detail": row.name, "original_qty": values["qty"]}
    if key not in origin:
        origin[key] = {**values, "sources": [source], "sle": sle,
                       "source_entry": doc.name, "source_detail": row.name}
        return
    pool = origin[key]
    if values.get("serial_no"):
        _fail("同一原序号有多次有效入向来源，请先核对原号的返库及转料顺序")
    if values.get("batch_no") != pool.get("batch_no"):
        _fail("原转料追踪身份不一致")
    pool["qty"] += values["qty"]
    pool["value"] += values["value"]
    pool["rate"] = pool["value"] / pool["qty"]
    matched = next((entry for entry in pool["sources"]
                    if (entry["stock_entry"], entry["detail"]) == (doc.name, row.name)), None)
    if matched:
        matched["original_qty"] += values["qty"]
    else:
        pool["sources"].append(source)
    if len(pool["sources"]) != 1:
        pool["source_entry"] = pool["source_detail"] = None


def _sources(pool, selected_qty=None):
    # These are proved capacities of the complete fungible same-price pool,
    # not invented historical outgoing-to-transfer assignments. A serial has
    # one exact source. SRE distributes current consumption within these caps.
    result = [dict(entry) for entry in pool["sources"]]
    if selected_qty is not None and len(result) == 1:
        result[0]["qty"] = selected_qty
    return result


def _batchwise(work, item, batch, *, read):
    if not _batch(batch, item.name, read=read).get("use_batchwise_valuation"):
        return False
    company = read("Company", work.company)
    company.check_permission("read")
    if company.name != work.company:
        _fail()
    method = item.get("valuation_method") or company.get("valuation_method")
    if method == "Moving Average":
        settings = read("Stock Settings", "Stock Settings")
        settings.check_permission("read")
        if settings.name != "Stock Settings":
            _fail()
        if settings.get("do_not_use_batchwise_valuation"):
            return False
    return True


def _valuation_policy(work, item, *, read):
    company = read("Company", work.company)
    company.check_permission("read")
    if company.name != work.company:
        _fail()
    method = item.get("valuation_method") or company.get("valuation_method")
    if method not in {"FIFO", "LIFO", "Moving Average"}:
        _fail("材料缺少明确原生库存估价方法")
    override = False
    if method == "Moving Average":
        settings = read("Stock Settings", "Stock Settings")
        settings.check_permission("read")
        if settings.name != "Stock Settings":
            _fail()
        override = bool(settings.get("do_not_use_batchwise_valuation"))
    return method, override


def _stock_queue(value):
    try:
        state = json.loads(value) if isinstance(value, str) else deepcopy(value or [])
    except (ValueError, TypeError):
        _fail("原生库存价层队列无法读取")
    if not isinstance(state, list) or len(state) > MAX_ROWS:
        _fail("原生库存价层队列不完整")
    result = []
    for layer in state:
        if not isinstance(layer, (list, tuple)) or len(layer) != 2:
            _fail("原生库存价层队列不准确")
        quantity, rate = map(_number, layer)
        if quantity < 0 or rate < 0:
            _fail("原生库存队列存在负价层，不能办理损耗")
        if quantity:
            result.append([float(quantity), float(rate)])
    return result


def _nonbatch_valuation(work, item, pools, ledger, *, read, records,
                         cutoff, exclude, before_creation):
    method, override = _valuation_policy(work, item, read=read)
    native_cutoff = cutoff
    if cutoff and before_creation is None:
        # All three installed non-batch selectors advance an unposted boundary
        # by exactly 1 ms. Use the same saved SLE/Bundle timestamps, including a
        # midnight crossing; Parent.creation is not a valuation-time substitute.
        boundary = datetime.fromisoformat(cutoff[0]) + timedelta(
            hours=float(cutoff[1][0]), minutes=float(cutoff[1][1]), seconds=float(cutoff[1][2]), milliseconds=1)
        native_cutoff = _stamp({"posting_date": boundary.date(), "posting_time": boundary.time()})
        stored = _records(SLE, {"company": work.company, "item_code": item.name, "warehouse": work.wip_warehouse,
            "is_cancelled": 0, "posting_date": ["<=", native_cutoff[0]]}, read=read, records=records)
        ledger = [sle for sle in stored if _stamp(sle) < native_cutoff
                  and not (sle.voucher_type == SE and sle.voucher_no == exclude)]
    for sle in ledger:
        if _stamp({"posting_date": _datetime(sle.get("posting_datetime")).date(),
                   "posting_time": _datetime(sle.get("posting_datetime")).time()}) != _stamp(sle):
            _fail("原生库存账保存时间与记账时间不一致，不能推测价层先后")
    ordered = sorted(ledger, key=lambda sle: (_stamp(sle), _datetime(sle.get("creation")), sle.name))
    if not ordered:
        _fail("材料缺少原生估价前序库存账")
    last = ordered[-1]
    whole_qty = sum((_number(sle.actual_qty) for sle in ledger), ZERO)
    whole_value = sum((_number(sle.stock_value_difference) for sle in ledger), ZERO)
    if (whole_qty < 0 or whole_value < 0
            or not _same(last.get("qty_after_transaction"), whole_qty, last, "actual_qty")
            or not _same(last.get("stock_value"), whole_value, last, "stock_value_difference")):
        _fail("原生前序库存余额与完整有效库存账不闭合")
    for sle in ordered:
        if (_stamp(sle), _datetime(sle.get("creation"))) == (_stamp(last), _datetime(last.get("creation"))) and (
                _number(sle.get("qty_after_transaction")) != _number(last.get("qty_after_transaction"))
                or _number(sle.get("stock_value")) != _number(last.get("stock_value"))
                or method == "FIFO" and _stock_queue(sle.get("stock_queue")) != _stock_queue(last.get("stock_queue"))):
            _fail("同一时刻前序库存账的原生余额或价层不唯一")
    state = _stock_queue(last.get("stock_queue")) if method == "FIFO" else []
    if state and (not _same(sum((_number(layer[0]) for layer in state), ZERO), whole_qty, last, "actual_qty")
            or not _same(sum((_number(layer[0]) * _number(layer[1]) for layer in state), ZERO),
                         whole_value, last, "stock_value_difference")):
        _fail("原生库存价层队列数量或金额与前序库存余额不一致")

    # These are exactly the two native non-batchwise history sources: legacy
    # SLE.batch_no plus active Bundle parents/children. Prove the full parent
    # selector too; it uses the parent's saved timestamp rather than child time.
    bundles = _records(BUNDLE, {"item_code": item.name, "warehouse": work.wip_warehouse,
        "docstatus": 1, "is_cancelled": 0, "type_of_transaction": ["in", ["Inward", "Outward"]]},
        read=read, records=records)
    owners = {sle.get("serial_and_batch_bundle"): sle for sle in ledger if sle.get("serial_and_batch_bundle")}
    if len(owners) != len([sle for sle in ledger if sle.get("serial_and_batch_bundle")]):
        _fail("原批次凭证存在重复库存账归属")
    bundle_qty, bundle_value = defaultdict(Decimal), defaultdict(Decimal)
    counted = set()
    for bundle in bundles:
        stamp = _stamp({"posting_date": _datetime(bundle.get("posting_datetime")).date(),
                        "posting_time": _datetime(bundle.get("posting_datetime")).time()})
        if (exclude and bundle.voucher_no == exclude or bundle.voucher_type == "Pick List"
                or native_cutoff and (stamp >= native_cutoff if before_creation is None else
                    stamp > native_cutoff or stamp == native_cutoff and bundle.name not in owners)):
            continue
        sle = owners.get(bundle.name)
        if not sle or stamp != _stamp(sle):
            _fail("原生整仓估价批次池缺少对应准确库存账，不能遗漏无账批次")
        counted.add(bundle.name)
        for batch, lot in _lots(sle, read=read, records=records).items():
            bundle_qty[batch] += lot["qty"]
            bundle_value[batch] += lot["value"]
    if counted != set(owners):
        _fail("原生整仓估价批次池不完整，不能遗漏隐藏凭证")
    legacy_qty, legacy_value = defaultdict(Decimal), defaultdict(Decimal)
    for sle in ledger:
        if sle.get("batch_no"):
            legacy_qty[sle.batch_no] += _number(sle.actual_qty)
            legacy_value[sle.batch_no] += _number(sle.stock_value_difference)
    cursor = {"method": method, "queue": state, "qty": whole_qty, "value": whole_value,
              "sle": last, "uses_fifo_queue": method == "FIFO" and bool(state)}
    for batch, pool in pools.items():
        if _batchwise(work, item, batch, read=read):
            continue
        native_qty = legacy_qty[batch] + bundle_qty[batch]
        native_value = legacy_value[batch] + bundle_value[batch]
        pool["native_available"] = native_qty
        pool["valuation"] = cursor
        # The installed legacy branch prices FIFO by its exact saved queue;
        # LIFO takes the native pooled balance on this SABB path. Moving Average
        # chooses its batch history or pooled balance via the native policy.
        if method == "Moving Average" and not override:
            rate = native_value / native_qty if native_qty > 0 and native_value > 0 else (
                whole_value / whole_qty if whole_qty > 0 and whole_value > 0 else ZERO)
        else:
            rate = whole_value / whole_qty if whole_qty > 0 and whole_value > 0 else ZERO
        pool["rate"] = rate


def _charge(pool, quantity):
    """Pure native pricing for one physical selection, sharing the item cursor."""
    quantity = _number(quantity)
    cursor = pool.get("valuation")
    if not cursor:
        return quantity * pool["rate"]
    if quantity > pool["native_available"] or quantity > cursor["qty"]:
        _fail("所选原批次超出原生估价可用数量")
    if cursor["uses_fifo_queue"]:
        from erpnext.stock.valuation import FIFOValuation

        native = FIFOValuation(deepcopy(cursor["queue"]))
        before_qty, before_value = native.get_total_stock_and_value()
        native.remove_stock(qty=float(quantity))
        after_qty, after_value = native.get_total_stock_and_value()
        amount = _number(before_value) - _number(after_value)
        if not _same(_number(before_qty) - _number(after_qty), quantity, cursor["sle"], "actual_qty"):
            _fail("原生FIFO价层数量不能准确核对")
        cursor["queue"] = deepcopy(native.state)
    else:
        amount = quantity * pool["rate"]
    if amount < 0 or amount > cursor["value"] and not _same(amount, cursor["value"], cursor["sle"], "stock_value_difference"):
        _fail("原生损耗金额超出真实库存剩余价值")
    cursor["qty"] -= quantity
    cursor["value"] -= amount
    pool["native_available"] -= quantity
    return amount


def _closing_equivalent(work, item, pools, ledger, *, read, records, cutoff):
    # Native uses the latest completed stock close no later than MAX submitted
    # PCV.period_end_date. Full ledger history must equal that seed; otherwise
    # it cannot be substituted into the native batch weighted-price equation.
    periods = _records(PERIOD_CLOSE, {"company": work.company, "docstatus": 1}, read=read, records=records)
    if not periods:
        return
    closed_upto = max(str(doc.period_end_date) for doc in periods)
    entries = _records(STOCK_CLOSE, {"company": work.company, "docstatus": 1, "status": "Completed",
        "to_date": ["<=", closed_upto]}, read=read, records=records)
    if not entries:
        return
    last = max(str(doc.to_date) for doc in entries)
    if cutoff and cutoff[0] <= last:
        return
    before = defaultdict(lambda: [ZERO, ZERO])
    for sle in ledger:
        if str(sle.posting_date) <= last:
            for batch, lot in _lots(sle, read=read, records=records).items():
                if batch in pools:
                    before[batch][0] += lot["qty"]
                    before[batch][1] += lot["value"]
    for entry in entries:
        if str(entry.to_date) != last:
            continue
        balances = _records(CLOSING_BALANCE, {"stock_closing_entry": entry.name, "item_code": item.name,
            "warehouse": work.wip_warehouse, "batch_no": ["in", sorted(pools)]}, read=read, records=records)
        totals = defaultdict(lambda: [ZERO, ZERO])
        for row in balances:
            if row.get("inventory_dimension_key"):
                continue
            totals[row.batch_no][0] += _number(row.actual_qty)
            totals[row.batch_no][1] += _number(row.stock_value_difference)
        for batch, pool in pools.items():
            if (not _same(totals[batch][0], before[batch][0], pool["sle"], "actual_qty")
                    or not _same(totals[batch][1], before[batch][1], pool["sle"], "stock_value_difference")):
                _fail("原生期末逐批数量或价值与完整历史账不一致，不能推测损耗价格")
    return last


def _native_batch_equivalent(work, item, pools, ledger, quantities, values, *, read, records,
                             cutoff, exclude, closed_date):
    # Native reads the child table, including older rows without an SLE join.
    # Prove that complete effective child query against the already proved SLE
    # owners; a hidden or orphan child must not silently change its price.
    fields = TABLES[BUNDLE][0][2]
    rows = records(ENTRY, {"item_code": item.name, "warehouse": work.wip_warehouse,
        "batch_no": ["in", sorted(pools)], "docstatus": 1, "is_cancelled": 0,
        "type_of_transaction": ["in", ["Inward", "Outward"]]}, fields, parenttype=BUNDLE)
    if len(rows) > MAX_ROWS or len({row.get("name") for row in rows}) != len(rows):
        _fail()
    proved, same_time_parents = {}, set()
    for sle in ledger:
        name = sle.get("serial_and_batch_bundle")
        if not name or closed_date and str(sle.posting_date) <= closed_date:
            continue
        same_time_parents.add(name)
        bundle = read(BUNDLE, name)
        bundle.check_permission("read")
        for row in bundle.get("entries") or []:
            if row.get("batch_no") not in pools:
                continue
            if row.name in proved:
                _fail("同一原批次凭证存在重复库存账归属，不能猜测原生估价")
            proved[row.name] = row
    counted = set()
    for row in rows:
        stamp = _stamp({"posting_date": _datetime(row.get("posting_datetime")).date(),
                        "posting_time": _datetime(row.get("posting_datetime")).time()})
        if (row.get("voucher_type") == "Pick List" or exclude and row.get("voucher_no") == exclude
                or closed_date and stamp[0] <= closed_date
                or cutoff and (stamp > cutoff or stamp == cutoff and row.get("parent") not in same_time_parents)):
            continue
        native = proved.get(row.get("name"))
        if not native or any(native.get(field) != value for field, value in row.items() if field in fields):
            _fail("原生估价批次子表与完整库存账来源不一致，不能遗漏隐藏或无账批次")
        counted.add(row["name"])
    if counted != set(proved):
        _fail("原生估价批次子表来源不完整，不能遗漏有效原账")

    # Native's deprecated query probes ANY nonempty SLE.batch_no and does not
    # exclude SABB mirrors. Those mirrored fields can contribute a second time.
    # Accept only when that exact native equation equals the physical remaining
    # value/quantity. Never average original layers or rewrite a mismatched SLE.
    extras = defaultdict(lambda: [ZERO, ZERO])
    for sle in ledger:
        batch = sle.get("batch_no")
        if (batch not in pools or not sle.get("serial_and_batch_bundle")
                or closed_date and str(sle.posting_date) <= closed_date):
            continue
        extras[batch][0] += _number(sle.actual_qty)
        extras[batch][1] += _number(sle.stock_value_difference)
    for batch in pools:
        quantity, value = quantities[batch], values[batch]
        native_qty, native_value = quantity + extras[batch][0], value + extras[batch][1]
        if quantity and (native_qty <= 0 or native_value * quantity != value * native_qty):
            _fail("原生旧批号字段与逐批凭证重复计价后的价格不等于真实剩余价值，需先核对原库存账")


def _pool(work, item, *, read, records, cutoff=None, exclude=None, before_creation=None):
    transfers = _transfers(work, read=read, records=records)
    _reposting(work, {item.name}, transfers, read=read, records=records)
    filters = {"company": work.company, "item_code": item.name, "warehouse": work.wip_warehouse, "is_cancelled": 0}
    if cutoff:
        filters["posting_date"] = ["<=", cutoff[0]]
    ledger = _records(SLE, filters, read=read, records=records)
    origin, current, current_value, all_rates = {}, defaultdict(Decimal), defaultdict(Decimal), set()
    seen_sources = defaultdict(Decimal)
    eligible = []
    for sle in sorted(ledger, key=lambda entry: (_stamp(entry), str(entry.get("creation") or ""), entry.name)):
        if not _precedes(sle, cutoff, before_creation) or sle.voucher_type == SE and sle.voucher_no == exclude:
            continue
        eligible.append(sle)
        if (sle.company != work.company or sle.item_code != item.name or sle.warehouse != work.wip_warehouse
                or sle.stock_uom != item.stock_uom or sle.get("is_cancelled")):
            _fail()
        lots = _lots(sle, read=read, records=records)
        key = sle.voucher_no, sle.voucher_detail_no, sle.item_code
        source = transfers.get(key) if sle.voucher_type == SE else None
        if source and _number(sle.actual_qty) > 0:
            doc, row = source
            if row.stock_uom != item.stock_uom or _stamp(sle) != _stamp(doc):
                _fail()
            _mirror(row, lots)
            seen_sources[key] += _number(sle.actual_qty)
            for batch, values in lots.items():
                _add_origin(origin, batch, values, doc, row, sle)
        for batch, values in lots.items():
            current[batch] += values["qty"]
            current_value[batch] += values["value"]
            all_rates.add(values["rate"])
    # A homogeneous price layer is also valid when native batch valuation is
    # disabled: moving average/FIFO/LIFO cannot invent a different lot price.
    heterogeneous = len(all_rates) != 1 and not (item.get("has_serial_no") and item.get("use_serial_no_wise_valuation"))
    batchwise = {}
    if heterogeneous:
        if item.get("has_serial_no") or not origin:
            _fail("序号材料存在多种历史价层且未启用逐号估值，不能猜测原号金额")
        batchwise = {batch: pool for batch, pool in origin.items() if _batchwise(work, item, batch, read=read)}
        if batchwise:
            closed_date = _closing_equivalent(work, item, batchwise, eligible, read=read, records=records, cutoff=cutoff)
            _native_batch_equivalent(work, item, batchwise, eligible, current, current_value,
                read=read, records=records, cutoff=cutoff, exclude=exclude, closed_date=closed_date)
        if len(batchwise) != len(origin):
            _nonbatch_valuation(work, item, origin, eligible,
                read=read, records=records, cutoff=cutoff, exclude=exclude, before_creation=before_creation)
    for key, amount in seen_sources.items():
        row = transfers[key][1]
        if not _same(amount, row.transfer_qty, row, "transfer_qty"):
            _fail("原工单转料库存账不完整，不能借用其他工单的批次")
    eligible_source_keys = {(sle.voucher_no, sle.voucher_detail_no, sle.item_code) for sle in eligible
                            if sle.voucher_type == SE and _number(sle.actual_qty) > 0}
    all_source_keys = {(sle.voucher_no, sle.voucher_detail_no, sle.item_code) for sle in ledger
                      if sle.voucher_type == SE and _number(sle.actual_qty) > 0}
    expected_sources = {key for key, (source, _row) in transfers.items() if key[2] == item.name and
                        (not cutoff or _stamp(source) < cutoff or _stamp(source) == cutoff and
                         (before_creation is None or key in eligible_source_keys or key not in all_source_keys))}
    if set(seen_sources) != expected_sources:
        _fail("原工单转料库存账或批次来源不完整，不能遗漏另一笔原转料")
    # The whole chosen pool belongs to this WO. Any actor's physical outflow
    # reduces it; foreign inward stock cannot expand its proved source capacity.
    for sle in eligible:
        if _number(sle.actual_qty) <= 0:
            continue
        for batch in _lots(sle, read=read, records=records):
            if batch in origin and (sle.voucher_type != SE or
                    (sle.voucher_no, sle.voucher_detail_no, sle.item_code) not in transfers):
                _fail("所选批号在制库存混有其他来源，不能将其他工单转料计入本工单损耗")
    for batch, values in origin.items():
        quantity = current[batch]
        if quantity < 0 or quantity > values["qty"]:
            _fail("原批号当前库存或累计耗用超过准确原转料数量")
        if (not values.get("valuation") and (current_value[batch] < 0
                or not quantity and not _same(current_value[batch], ZERO, values["sle"], "stock_value_difference"))):
            _fail("原批次有效库存数量与剩余价值不闭合")
        values["available"] = quantity
        if heterogeneous and quantity and batch in batchwise:
            values["rate"] = current_value[batch] / quantity
    return origin


def _historical_origins(work, item, *, read, records, cutoff, before_creation=None):
    """Only original transfer facts; cancellation does not revalue current WIP."""
    transfers = _transfers(work, read=read, records=records)
    result = {}
    for (voucher, detail, code), (doc, row) in transfers.items():
        if code != item.name or _stamp(doc) > cutoff:
            continue
        ledger = _records(SLE, {"company": work.company, "voucher_type": SE, "voucher_no": voucher,
            "voucher_detail_no": detail, "item_code": code, "warehouse": work.wip_warehouse, "is_cancelled": 0},
            read=read, records=records)
        if ledger and before_creation is not None:
            ledger = [sle for sle in ledger if _precedes(sle, cutoff, before_creation)]
            if not ledger:
                continue
        qty = ZERO
        for sle in ledger:
            if _number(sle.actual_qty) <= 0 or sle.stock_uom != item.stock_uom or _stamp(sle) != _stamp(doc):
                _fail()
            qty += _number(sle.actual_qty)
            lots = _lots(sle, read=read, records=records)
            _mirror(row, lots)
            for batch, values in lots.items():
                _add_origin(result, batch, values, doc, row, sle)
        if not ledger or not _same(qty, row.transfer_qty, row, "transfer_qty"):
            _fail("撤销损耗缺少完整原工单批次转料证据")
    return result


def proposal(work, requested_by_item, *, read, records, posting_date=None, posting_time=None):
    result = []
    cutoff = _stamp({"posting_date": posting_date, "posting_time": posting_time}) if posting_date else None
    for code, requested in sorted(requested_by_item.items()):
        item = read("Item", code)
        item.check_permission("read")
        if not (item.get("has_batch_no") or item.get("has_serial_no")):
            continue
        remaining = _number(requested)
        if (remaining <= 0 or not item.get("is_stock_item")
                or item.get("has_serial_no") and remaining != remaining.to_integral_value()):
            _fail()
        pools = _pool(work, item, read=read, records=records, cutoff=cutoff)
        modes = set()
        for _key, pool in sorted(pools.items(), key=lambda pair:
                (_stamp(pair[1]["sle"]), pair[1]["sources"][0]["stock_entry"], str(pair[0]))):
            qty = min(remaining, pool["available"])
            if not qty:
                continue
            modes.add(bool(pool.get("valuation")))
            if len(modes) > 1:
                _fail("同一材料本次损耗不能混选逐批和非逐批估价批号，尚不能完整核对原生价层，请先核对原批次来源")
            batch, serial = pool["batch_no"], pool["serial_no"]
            if item.get("has_batch_no") and _batch(batch, code, read=read).get("disabled"):
                _fail("所选原批号已停用，不能新增损耗出库")
            if serial:
                if qty != 1:
                    _fail("序号材料必须按原号的整数库存数量办理")
                _serial(serial, code, work.company, batch, read=read, current_warehouse=work.wip_warehouse)
            value = _charge(pool, qty)
            result.append({"item_code": code, "batch_no": batch, "qty": qty, "stock_uom": item.stock_uom,
                "uom": item.stock_uom, "conversion_factor": 1, "use_serial_batch_fields": 1,
                "expected_value": value, "allow_zero_valuation_rate": int(value == 0),
                "source_transfers": _sources(pool, qty)})
            if serial:
                result[-1]["serial_no"] = serial
            remaining -= qty
            if not remaining:
                break
        if remaining:
            _fail("原工单准确批号在制库存不足，不能借用其他批次或工单库存")
    return result


def prove_loss_batches(doc, work, *, read, records, posted=False):
    result = {}
    historical, pools_by_item, sequences, modes = doc.docstatus == 2, {}, {}, {}
    for row in doc.get("items") or []:
        item = read("Item", row.item_code)
        if not (item.get("has_batch_no") or item.get("has_serial_no")):
            continue
        item.check_permission("read")
        quantity = _number(row.transfer_qty)
        if (not item.get("is_stock_item") or quantity <= 0 or row.stock_uom != item.stock_uom
                or item.get("has_serial_no") and quantity != quantity.to_integral_value()
                or row.s_warehouse != work.wip_warehouse or row.get("t_warehouse")):
            _fail()
        if posted or historical:
            filters = {"company": work.company, "voucher_type": SE, "voucher_no": doc.name,
                "voucher_detail_no": row.name, "item_code": row.item_code, "warehouse": work.wip_warehouse,
                "is_cancelled": ["in", [0, 1]] if historical else 0, "actual_qty": ["<", 0]}
            ledger = _records(SLE, filters, read=read, records=records)
            lots, value, qty = {}, ZERO, ZERO
            for sle in ledger:
                if sle.stock_uom != item.stock_uom or _stamp(sle) != _stamp(doc):
                    _fail()
                for batch, values in _lots(sle, read=read, records=records, historical=historical).items():
                    if batch in lots:
                        _fail()
                    lots[batch] = values
                    value -= values["value"]
                    qty -= values["qty"]
            if (not ledger or not lots or (not item.get("has_serial_no") and len(lots) != 1)
                    or not _same(qty, quantity, row, "transfer_qty")):
                _fail("损耗出库缺少完整原批次库存账，不能按当前均价补账")
            if row.get("serial_and_batch_bundle") and any(sle.get("serial_and_batch_bundle") != row.serial_and_batch_bundle for sle in ledger):
                _fail()
        else:
            lots = _selected_lots(doc, row, item, read=read, records=records)
            value = None
        if row.item_code not in pools_by_item:
            pools_by_item[row.item_code] = (_historical_origins(work, item, read=read, records=records, cutoff=_stamp(doc),
                    before_creation=min((_datetime(entry.get("creation")) for entry in ledger), default=None))
                if historical else _pool(work, item, read=read, records=records,
                    cutoff=_stamp(doc), exclude=doc.name,
                    before_creation=min((_datetime(entry.get("creation")) for entry in ledger), default=None) if posted else None))
        pools = pools_by_item[row.item_code]
        if not historical:
            selected_modes = modes.setdefault(row.item_code, set())
            selected_modes.update(bool(pools[key].get("valuation")) for key in lots if key in pools)
            if len(selected_modes) > 1:
                _fail("同一材料本次损耗不能混选逐批和非逐批估价批号，尚不能完整核对原生价层，请先核对原批次来源")
        if not historical and any(pools[key].get("valuation") for key in lots if key in pools):
            sequence = _number(row.get("idx"))
            if sequence != sequence.to_integral_value() or sequence <= sequences.get(row.item_code, ZERO):
                _fail("非逐批估价损耗明细必须保留原生保存顺序，不能调换价层扣减")
            sequences[row.item_code] = sequence
        sources, detail_lots, expected = [], [], ZERO
        for batch, chosen in lots.items():
            if historical:
                original = pools.get(batch)
                if (not original or -chosen["qty"] > original.setdefault("available", original["qty"])
                        or chosen.get("serial_no") and not _same(
                            -chosen["value"], -chosen["qty"] * original["rate"], row, "amount")):
                    _fail("原损耗批号或历史金额与原工单转料不一致")
                original["available"] += chosen["qty"]
                expected -= chosen["value"]
                lot_sources = _sources(original, -chosen["qty"])
                sources.extend(lot_sources)
                detail_lots.append({"lot_key": batch, "qty": -chosen["qty"], "value": -chosen["value"],
                    "batch_no": chosen["batch_no"], "serial_no": chosen["serial_no"], "sources": lot_sources})
                continue
            pool = pools.get(batch)
            if not pool or -chosen["qty"] > pool["available"]:
                _fail("所选批号不在原工单准确转料池内，或累计出库超过剩余数量")
            amount = _charge(pool, -chosen["qty"])
            if (value is not None and not _same(-chosen["value"], amount, row, "amount")):
                _fail("原生逐号或逐批损耗金额与准确原转料价层不一致")
            expected += amount
            lot_sources = _sources(pool, -chosen["qty"])
            sources.extend(lot_sources)
            detail_lots.append({"lot_key": batch, "qty": -chosen["qty"], "value": amount,
                "batch_no": chosen["batch_no"], "serial_no": chosen["serial_no"], "sources": lot_sources})
            pool["available"] += chosen["qty"]
        if value is not None and not _same(value, expected, row, "amount"):
            _fail("原生批号实际损耗金额与原转料单一价层不一致，不能改账凑齐")
        cursor = next((pools[key].get("valuation") for key in lots if pools[key].get("valuation")), None)
        if value is not None and not historical and cursor and cursor["uses_fifo_queue"]:
            if len(ledger) != 1 or (not _same(ledger[0].get("qty_after_transaction"), cursor["qty"], ledger[0], "actual_qty")
                    or not _same(ledger[0].get("stock_value"), cursor["value"], ledger[0], "stock_value_difference")
                    or _stock_queue(ledger[0].get("stock_queue")) != _stock_queue(cursor["queue"])):
                _fail("原生FIFO损耗账的结余数量、价值或价层与真实扣减不一致")
        _mirror(row, lots)
        batches = {lot["batch_no"] for lot in lots.values()}
        result[row.name] = {"qty": quantity, "value": expected, "rate": expected / quantity,
            "batch_no": next(iter(batches)) if len(batches) == 1 else None, "sources": sources,
            "lots": detail_lots, "serial_nos": [lot["serial_no"] for lot in lots.values() if lot["serial_no"]]}
    return result


def consumed_batches(work, *, read, records, exclude=None):
    """Exact WO batch outflows and original transfer keys for native SRE tails.

    Manufacturing consumption, transfers returned out of WIP and marked loss
    issues leave this original pool. Foreign WO quantities are never copied to
    this WO's reservation rows. Zero consumption remains explicit for cancel.
    """
    native = _records(SE, {"company": work.company, "work_order": work.name, "docstatus": 1,
        "purpose": ["in", ["Material Transfer for Manufacture", "Manufacture", "Material Consumption for Manufacture"]]},
        read=read, records=records)
    losses = _records(SE, {"company": work.company, LOSS_WORK: work.name, "docstatus": 1}, read=read, records=records)
    documents = {doc.name: doc for doc in native + losses if doc.name != exclude}
    for doc in documents.values():
        if (doc.company != work.company or doc.docstatus != 1
                or (doc.get(LOSS_WORK) == work.name and (doc.purpose != "Material Issue" or doc.get("work_order")))
                or (not doc.get(LOSS_WORK) and doc.get("work_order") != work.name)):
            _fail("批号耗用凭证与准确原工单不一致")
    result = {}
    items = sorted({row.item_code for row in work.get("required_items") or []})
    for code in items:
        item = read("Item", code)
        if not (item.get("has_batch_no") or item.get("has_serial_no")):
            continue
        pools = _pool(work, item, read=read, records=records, exclude=exclude)
        for batch, pool in pools.items():
            result[code, batch] = {"qty": ZERO, "value": ZERO, "original_qty": pool["qty"],
                "source_entry": pool["source_entry"], "source_detail": pool["source_detail"],
                "available_qty": pool["available"], "batch_no": pool["batch_no"], "serial_no": pool["serial_no"],
                "sources": _sources(pool)}
        if not documents:
            continue
        ledger = _records(SLE, {"company": work.company, "item_code": code, "warehouse": work.wip_warehouse,
            "voucher_type": SE, "voucher_no": ["in", sorted(documents)], "is_cancelled": 0, "actual_qty": ["<", 0]},
            read=read, records=records)
        details_qty = defaultdict(Decimal)
        for sle in ledger:
            parent = documents.get(sle.voucher_no)
            matches = [row for row in parent.get("items") or [] if row.name == sle.voucher_detail_no] if parent else []
            if (len(matches) != 1 or matches[0].item_code != code or matches[0].s_warehouse != work.wip_warehouse
                    or matches[0].stock_uom != item.stock_uom or sle.stock_uom != item.stock_uom
                    or _stamp(sle) != _stamp(parent)):
                _fail("原工单批号耗用缺少准确原单、原行或在制仓来源")
            details_qty[sle.voucher_no, sle.voucher_detail_no] -= _number(sle.actual_qty)
            for batch, values in _lots(sle, read=read, records=records).items():
                consumed = result.get((code, batch))
                if not consumed:
                    _fail("批号耗用不属于原工单准确转料池")
                consumed["qty"] -= values["qty"]
                consumed["value"] -= values["value"]
        for parent in documents.values():
            for row in parent.get("items") or []:
                if row.item_code == code and row.get("s_warehouse") == work.wip_warehouse:
                    if (row.parent != parent.name or row.parenttype != SE or row.parentfield != "items"
                            or not _same(details_qty[parent.name, row.name], row.transfer_qty, row, "transfer_qty")):
                        _fail("原工单批号耗用库存账不完整，不能按汇总数量分给预留批次")
        for (item_code, batch), consumed in result.items():
            if item_code == code and consumed["qty"] > pools[batch]["qty"]:
                _fail("原工单批号累计耗用超过准确原转料数量")
    return result
