"""Prove batch materials from their exact Work Order WIP transfer history.

Every read is supplied by the caller. The costing Session owns permission and
read-change closure; stock writers supply current NOWAIT readers. This module
never selects, writes, creates bundles or overrides native valuation itself.
"""

from collections import defaultdict
from decimal import Decimal, InvalidOperation

import frappe
from frappe.utils import flt


SE = "Stock Entry"
SLE = "Stock Ledger Entry"
BUNDLE = "Serial and Batch Bundle"
ENTRY = "Serial and Batch Entry"
REPOST = "Repost Item Valuation"
LOSS_WORK = "jf_production_loss_work_order"
MAX_ROWS = 10000
ZERO = Decimal(0)
IDENTITY = {"name", "docstatus", "modified"}
CHILD = IDENTITY | {"parent", "parenttype", "parentfield"}
PARENTS = {
    SE: IDENTITY | {"company", "purpose", "work_order", LOSS_WORK, "posting_date", "posting_time", "is_return"},
    SLE: IDENTITY | {"company", "item_code", "warehouse", "stock_uom", "voucher_type", "voucher_no",
        "voucher_detail_no", "actual_qty", "stock_value_difference", "serial_and_batch_bundle", "batch_no",
        "serial_no", "is_cancelled", "is_adjustment_entry", "posting_date", "posting_time", "creation"},
    BUNDLE: IDENTITY | {"company", "item_code", "warehouse", "voucher_type", "voucher_no", "voucher_detail_no",
        "type_of_transaction", "is_cancelled", "has_batch_no", "has_serial_no", "total_qty", "total_amount"},
    "Batch": IDENTITY | {"item", "disabled", "use_batchwise_valuation"},
    "Item": {"name", "modified", "stock_uom", "is_stock_item", "has_batch_no", "has_serial_no"},
    REPOST: IDENTITY | {"company", "status", "item_code", "warehouse", "voucher_type", "voucher_no",
        "based_on", "reposting_data_file", "items_to_be_repost"},
}
TABLES = {
    SE: (("items", "Stock Entry Detail", CHILD | {"item_code", "stock_uom", "qty", "uom", "conversion_factor",
        "transfer_qty", "s_warehouse", "t_warehouse", "serial_no", "batch_no", "serial_and_batch_bundle",
        "use_serial_batch_fields", "basic_rate", "basic_amount", "amount", "valuation_rate"}),),
    BUNDLE: (("entries", ENTRY, CHILD | {"item_code", "warehouse", "batch_no", "serial_no", "qty",
        "incoming_rate", "stock_value_difference", "is_cancelled"}),),
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


def _stamp(doc):
    # ISO dates and native posting_time strings/timedeltas preserve ordering
    # after padding the clock fields (timedelta string may start with 0:).
    date = str(doc.get("posting_date") or "")
    clock = str(doc.get("posting_time") or "00:00:00")
    pieces = clock.split(":")
    if not date or len(pieces) != 3:
        _fail("批号库存来源缺少准确记账日期时间")
    return date, tuple(Decimal(part) for part in pieces)


def _lots(sle, *, read, records, historical=False):
    qty, value = _number(sle.actual_qty), _number(sle.stock_value_difference)
    if (sle.docstatus != 1 or not qty or sle.get("is_adjustment_entry") or sle.get("serial_no")
            or qty * value < 0):
        _fail("批号库存账方向、状态或价值层无法唯一核对")
    name = sle.get("serial_and_batch_bundle")
    if not name:
        batch = sle.get("batch_no")
        _batch(batch, sle.item_code, read=read)
        return {batch: {"qty": qty, "value": value, "rate": value / qty}}
    bundle = read(BUNDLE, name)
    bundle.check_permission("read")
    allowed = {1, 2} if historical else {1}
    if (bundle.docstatus not in allowed or (not historical and bundle.get("is_cancelled"))
            or bundle.company != sle.company or bundle.item_code != sle.item_code or bundle.warehouse != sle.warehouse
            or bundle.get("has_serial_no") or not bundle.get("has_batch_no")
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
                or row.docstatus != bundle.docstatus or row.get("serial_no")
                or row.item_code != sle.item_code or row.warehouse != sle.warehouse
                or (not historical and row.get("is_cancelled"))):
            _fail("原批号凭证明细的物料、数量或父凭证不一致")
        batch, quantity = row.get("batch_no"), _number(row.qty)
        rate, amount = _number(row.get("incoming_rate")), _number(row.get("stock_value_difference"))
        if (not batch or batch in result or not quantity or quantity * qty <= 0 or rate < 0
                or not _same(amount, quantity * rate, row, "stock_value_difference")):
            _fail("同批重复或存在无法准确区分的历史价层，请逐层核对原单")
        _batch(batch, sle.item_code, read=read)
        result[batch] = {"qty": quantity, "value": amount, "rate": rate}
    if (not _same(sum((row["qty"] for row in result.values()), ZERO), qty, sle, "actual_qty")
            or not _same(sum((row["value"] for row in result.values()), ZERO), value, sle, "stock_value_difference")
            or not _same(bundle.total_qty, qty, sle, "actual_qty")
            or not _same(bundle.total_amount, value, sle, "stock_value_difference")):
        _fail("逐批数量或价值与确切库存账不一致")
    if sle.get("batch_no") and set(result) != {sle.batch_no}:
        _fail("库存账旧批号字段与批号凭证不一致")
    return result


def _selected_batch(doc, row, item, *, read, records):
    """Validate a native-created draft bundle without trusting its valuation."""
    batch = row.get("batch_no")
    if row.get("serial_and_batch_bundle"):
        bundle = read(BUNDLE, row.serial_and_batch_bundle)
        bundle.check_permission("read")
        if (bundle.docstatus != 0 or bundle.get("is_cancelled") or bundle.company != doc.company
                or bundle.item_code != item.name or bundle.warehouse != row.s_warehouse
                or bundle.voucher_type != SE or bundle.voucher_no != doc.name
                or bundle.voucher_detail_no != row.name or bundle.type_of_transaction != "Outward"
                or bundle.get("has_serial_no") or not bundle.get("has_batch_no")):
            _fail("损耗批号凭证必须是本单、本行准确新建的原生出向草稿")
        fields = TABLES[BUNDLE][0][2]
        entries = records(ENTRY, {"parent": bundle.name}, fields, parenttype=BUNDLE)
        actual = bundle.get("entries") or []
        if len(entries) != 1 or len(actual) != 1:
            _fail("请按一行一批办理损耗，不能合并或遗漏原批号明细")
        entry, native = entries[0], actual[0]
        if (any(native.get(field) != value for field, value in entry.items() if field in fields)
                or entry.get("parent") != bundle.name or entry.get("parenttype") != BUNDLE
                or entry.get("parentfield") != "entries" or entry.get("docstatus") != 0
                or entry.get("is_cancelled") or entry.get("serial_no")
                or entry.get("item_code") != item.name or entry.get("warehouse") != row.s_warehouse
                or not _same(-_number(entry.get("qty")), row.transfer_qty, row, "transfer_qty")
                or (batch and batch != entry.get("batch_no"))):
            _fail("新出向批号凭证与本次损耗选择不一致")
        batch = entry.get("batch_no")
    selected = _batch(batch, item.name, read=read)
    if selected.get("disabled"):
        _fail("所选原批号已停用，不能新增损耗出库")
    return batch


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


def _pool(work, item, *, read, records, cutoff=None, exclude=None):
    transfers = _transfers(work, read=read, records=records)
    _reposting(work, {item.name}, transfers, read=read, records=records)
    filters = {"company": work.company, "item_code": item.name, "warehouse": work.wip_warehouse, "is_cancelled": 0}
    if cutoff:
        filters["posting_date"] = ["<=", cutoff[0]]
    ledger = _records(SLE, filters, read=read, records=records)
    origin, current, all_rates = {}, defaultdict(Decimal), set()
    seen_sources = defaultdict(Decimal)
    for sle in sorted(ledger, key=lambda entry: (_stamp(entry), str(entry.get("creation") or ""), entry.name)):
        if cutoff and _stamp(sle) > cutoff or sle.voucher_type == SE and sle.voucher_no == exclude:
            continue
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
            seen_sources[key] += _number(sle.actual_qty)
            for batch, values in lots.items():
                if batch in origin:
                    _fail("同一批号有多笔原转料来源，当前入口不能猜测逐层预留和历史价值")
                origin[batch] = {**values, "source_entry": doc.name, "source_detail": row.name, "sle": sle}
        for batch, values in lots.items():
            current[batch] += values["qty"]
            all_rates.add(values["rate"])
    # A homogeneous price layer is also valid when native batch valuation is
    # disabled: moving average/FIFO/LIFO cannot invent a different lot price.
    if len(all_rates) != 1:
        _fail("在制仓该材料存在多种历史价层，当前入口不能推测原批出库价值")
    for key, amount in seen_sources.items():
        row = transfers[key][1]
        if not _same(amount, row.transfer_qty, row, "transfer_qty"):
            _fail("原工单转料库存账不完整，不能借用其他工单的批次")
    expected_sources = {key for key, (source, _row) in transfers.items() if key[2] == item.name
                        and (not cutoff or _stamp(source) <= cutoff)}
    if set(seen_sources) != expected_sources:
        _fail("原工单转料库存账或批次来源不完整，不能遗漏另一笔原转料")
    # Every chosen lot must have exactly one positive origin and no foreign
    # inward movement. Outward use/returns by any actor reduce the physical pool.
    for sle in ledger:
        if cutoff and _stamp(sle) > cutoff or sle.voucher_type == SE and sle.voucher_no == exclude:
            continue
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
        values["available"] = quantity
    return origin


def _historical_origins(work, item, *, read, records, cutoff):
    """Only original transfer facts; cancellation does not revalue current WIP."""
    transfers = _transfers(work, read=read, records=records)
    result = {}
    for (voucher, detail, code), (doc, row) in transfers.items():
        if code != item.name or _stamp(doc) > cutoff:
            continue
        ledger = _records(SLE, {"company": work.company, "voucher_type": SE, "voucher_no": voucher,
            "voucher_detail_no": detail, "item_code": code, "warehouse": work.wip_warehouse, "is_cancelled": 0},
            read=read, records=records)
        qty = ZERO
        for sle in ledger:
            if _number(sle.actual_qty) <= 0 or sle.stock_uom != item.stock_uom or _stamp(sle) != _stamp(doc):
                _fail()
            qty += _number(sle.actual_qty)
            for batch, values in _lots(sle, read=read, records=records).items():
                if batch in result:
                    _fail("原批号有多个转料来源，无法准确证明原损耗归属")
                result[batch] = {**values, "source_entry": voucher, "source_detail": detail}
        if not ledger or not _same(qty, row.transfer_qty, row, "transfer_qty"):
            _fail("撤销损耗缺少完整原工单批次转料证据")
    return result


def proposal(work, requested_by_item, *, read, records, posting_date=None, posting_time=None):
    result = []
    cutoff = _stamp({"posting_date": posting_date, "posting_time": posting_time}) if posting_date else None
    for code, requested in sorted(requested_by_item.items()):
        item = read("Item", code)
        item.check_permission("read")
        if item.get("has_serial_no"):
            _fail("序号原料全损耗须逐号核对，当前批号入口暂不支持")
        if not item.get("has_batch_no"):
            continue
        remaining = _number(requested)
        if remaining <= 0 or not item.get("is_stock_item"):
            _fail()
        pools = _pool(work, item, read=read, records=records, cutoff=cutoff)
        for batch, pool in sorted(pools.items(), key=lambda pair: (_stamp(pair[1]["sle"]), pair[1]["source_entry"], pair[0])):
            qty = min(remaining, pool["available"])
            if not qty:
                continue
            batch_doc = _batch(batch, code, read=read)
            if batch_doc.get("disabled"):
                _fail("所选原批号已停用，不能新增损耗出库")
            value = qty * pool["rate"]
            result.append({"item_code": code, "batch_no": batch, "qty": qty, "stock_uom": item.stock_uom,
                "uom": item.stock_uom, "conversion_factor": 1, "use_serial_batch_fields": 1,
                "expected_value": value, "allow_zero_valuation_rate": int(pool["rate"] == 0),
                "source_transfers": [{"stock_entry": pool["source_entry"], "detail": pool["source_detail"], "qty": qty}]})
            remaining -= qty
            if not remaining:
                break
        if remaining:
            _fail("原工单准确批号在制库存不足，不能借用其他批次或工单库存")
    return result


def prove_loss_batches(doc, work, *, read, records, posted=False):
    result = {}
    historical, pools_by_item = doc.docstatus == 2, {}
    for row in doc.get("items") or []:
        item = read("Item", row.item_code)
        if item.get("has_serial_no"):
            _fail("序号原料全损耗须逐号核对，当前批号入口暂不支持")
        if not item.get("has_batch_no"):
            continue
        item.check_permission("read")
        quantity = _number(row.transfer_qty)
        if (not item.get("is_stock_item") or quantity <= 0 or row.get("serial_no") or row.stock_uom != item.stock_uom
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
            if not ledger or len(lots) != 1 or not _same(qty, quantity, row, "transfer_qty"):
                _fail("损耗出库缺少完整原批次库存账，不能按当前均价补账")
            if row.get("serial_and_batch_bundle") and any(sle.get("serial_and_batch_bundle") != row.serial_and_batch_bundle for sle in ledger):
                _fail()
        else:
            batch = _selected_batch(doc, row, item, read=read, records=records)
            lots = {batch: {"qty": -quantity}}
            value = None
        if row.item_code not in pools_by_item:
            pools_by_item[row.item_code] = (_historical_origins(work, item, read=read, records=records, cutoff=_stamp(doc))
                if historical else _pool(work, item, read=read, records=records,
                                         cutoff=_stamp(doc) if posted else None, exclude=doc.name))
        pools = pools_by_item[row.item_code]
        sources, expected = [], ZERO
        for batch, chosen in lots.items():
            if historical:
                original = pools.get(batch)
                if (not original or -chosen["qty"] > original.setdefault("available", original["qty"])
                        or not _same(-chosen["value"], -chosen["qty"] * original["rate"], row, "amount")):
                    _fail("原损耗批号或历史金额与原工单转料不一致")
                original["available"] += chosen["qty"]
                expected -= chosen["value"]
                sources.append({"stock_entry": original["source_entry"], "detail": original["source_detail"], "qty": -chosen["qty"]})
                continue
            pool = pools.get(batch)
            if not pool or -chosen["qty"] > pool["available"]:
                _fail("所选批号不在原工单准确转料池内，或累计出库超过剩余数量")
            expected += -chosen["qty"] * pool["rate"]
            sources.append({"stock_entry": pool["source_entry"], "detail": pool["source_detail"], "qty": -chosen["qty"]})
            pool["available"] += chosen["qty"]
        if value is not None and not _same(value, expected, row, "amount"):
            _fail("原生批号实际损耗金额与原转料单一价层不一致，不能改账凑齐")
        if row.get("batch_no") and set(lots) != {row.batch_no}:
            _fail("损耗明细所选批号与真实出库批号不一致")
        result[row.name] = {"qty": quantity, "value": expected, "rate": expected / quantity,
            "batch_no": next(iter(lots)) if len(lots) == 1 else None, "sources": sources}
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
        if item.get("has_serial_no"):
            _fail("序号原料消费预留须逐号核对，当前批号入口暂不支持")
        if not item.get("has_batch_no"):
            continue
        pools = _pool(work, item, read=read, records=records, exclude=exclude)
        for batch, pool in pools.items():
            result[code, batch] = {"qty": ZERO, "value": ZERO, "original_qty": pool["qty"],
                "source_entry": pool["source_entry"], "source_detail": pool["source_detail"],
                "available_qty": pool["available"]}
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
