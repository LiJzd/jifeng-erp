"""Bound native subcontract returns by exact receipt rows and physical pools.

Subcontracting Receipt skips AccountsController.validate, including its return
checks. Keep the native stock, accounting, status and cancellation lifecycle;
add the missing checks before its calculation and repair its draft mapper.
"""

from decimal import Decimal, InvalidOperation

import frappe
from frappe.utils import cint, flt, get_datetime


RECEIPT = "Subcontracting Receipt"
_ROW_IDENTITY = ("item_code", "stock_uom", "subcontracting_order",
                 "subcontracting_order_item", "purchase_order", "purchase_order_item",
                 "job_card", "bom", "jf_flow_order", "secondary_item_type", "valuation_type")


def _number(value):
    try:
        value = Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw("委外退回数量或换算系数不是有效数字")
    if not value.is_finite():
        frappe.throw("委外退回数量或换算系数不是有限数字")
    return value


def _factor(row):
    factor = _number(row.get("conversion_factor"))
    if factor <= 0:
        frappe.throw("委外退回库存单位换算系数必须大于零")
    return factor


def _precision(row, field="qty"):
    if field == "qty" and row.get("doctype") == "Subcontracting Receipt Supplied Item":
        field = "consumed_qty"
    method = getattr(row, "precision", None)
    value = method(field) if callable(method) else None
    return int(value if value is not None else 6)


def _same(left, right, row, field="qty"):
    return flt(left, _precision(row, field)) == flt(right, _precision(row, field))


def _covered_stock(row):
    # v16 persists received_qty = accepted + rejected + confirmed loss and
    # bases supplied material consumption on received_qty * conversion_factor.
    if row.get("received_qty") in (None, ""):
        frappe.throw("原收回缺少已过账覆盖数量，不能猜测耗料分母")
    received = _number(row.received_qty)
    components = sum((_number(row.get(field)) for field in
                      ("qty", "rejected_qty", "process_loss_qty")), Decimal(0))
    if received <= 0 or not _same(received, components, row, "received_qty"):
        frappe.throw("原收回覆盖数量与接收、拒收和保留损耗不一致")
    return received * _factor(row)


def _read_fields(doctype, fields, parenttype=None):
    from frappe.model import get_permitted_fields

    meta = frappe.get_meta(doctype)
    fields = {field for field in fields if meta.get_field(field)}
    allowed = set(get_permitted_fields(doctype, parenttype=parenttype,
                                      permission_type="read", ignore_virtual=True))
    if not fields.issubset(allowed):
        frappe.throw("没有完整读取原收回数量、耗料或费用来源字段的权限", frappe.PermissionError)
    levels = meta.get_permlevel_access(permission_type="mask", parenttype=parenttype,
                                       user=frappe.session.user)
    if frappe.session.user != "Administrator" and any(
            meta.get_field(field).get("mask") and (meta.get_field(field).permlevel or 0) not in levels
            for field in fields):
        frappe.throw("没有读取原收回被遮蔽来源字段的权限", frappe.PermissionError)


def _read_source_fields(source):
    _read_fields(RECEIPT, {"company", "supplier", "supplier_warehouse", "posting_date", "posting_time",
                           "docstatus", "is_return"})
    for table, child in (("items", "Subcontracting Receipt Item"),
                         ("supplied_items", "Subcontracting Receipt Supplied Item"),
                         ("additional_costs", "Landed Cost Taxes and Charges")):
        # get_permitted_fields projects database columns and excludes Tables.
        # Check the parent Table's actual read/mask level independently; still
        # enforce the full child field projection before copying source facts.
        meta = frappe.get_meta(RECEIPT)
        field = meta.get_field(table)
        levels = set(meta.get_permlevel_access(permission_type="read", user=frappe.session.user))
        if 0 not in levels and frappe.share.get_shared(RECEIPT, frappe.session.user,
                                                       rights=["read"], limit=1):
            levels.add(0)
        if (not field or field.fieldtype != "Table" or field.options != child
                or (field.permlevel or 0) not in levels):
            frappe.throw("没有完整读取原收回明细表的权限", frappe.PermissionError)
        mask_levels = meta.get_permlevel_access(permission_type="mask", user=frappe.session.user)
        if (frappe.session.user != "Administrator" and field.get("mask")
                and (field.permlevel or 0) not in mask_levels):
            frappe.throw("没有读取原收回被遮蔽明细表的权限", frappe.PermissionError)
        fields = {field for row in source.get(table) or [] for field in row.as_dict()
                  if not field.startswith("_")}
        _read_fields(child, fields, RECEIPT)


def _source(name, document=None):
    if not isinstance(name, str) or not name.strip():
        frappe.throw("委外实物退回必须从原收回凭证生成")
    # Keep this parent lock until the caller's normal Frappe commit. All return
    # saves/submissions lock the same source before reading remaining pools.
    source = frappe.get_doc(RECEIPT, name, for_update=True)
    source.check_permission("read")
    _read_source_fields(source)
    if source.docstatus != 1 or cint(source.get("is_return")):
        frappe.throw("委外退回来源必须是已提交且未取消的原收回凭证")
    if document is not None:
        if (source.company != document.company or source.supplier != document.supplier
                or source.get("supplier_warehouse") != document.get("supplier_warehouse")):
            frappe.throw("委外退回的公司、供应商或供应仓与原收回不一致")
        posting = "{} {}".format(document.posting_date, document.get("posting_time") or "00:00:00")
        original = "{} {}".format(source.posting_date, source.get("posting_time") or "00:00:00")
        if get_datetime(posting) < get_datetime(original):
            frappe.throw("委外退回过账时间不能早于原收回凭证")
    return source


def _physical_pools(row, source):
    """Return positive stock quantities from the persisted warehouses, no flag."""
    if any((row.get(field) or "") != (source.get(field) or "") for field in _ROW_IDENTITY):
        frappe.throw("委外退回的商品、单位、订单明细或工序来源与原收回不一致")
    factor = _factor(row)
    qty, rejected = _number(row.get("qty")), _number(row.get("rejected_qty"))
    if qty > 0 or rejected > 0 or _number(row.get("process_loss_qty")) != 0:
        frappe.throw("委外退回只能退回负数实物数量，原收回工艺损耗须保留，退回损耗必须为0")
    accepted_qty, rejected_qty = Decimal(0), -rejected * factor
    if qty:
        from_accepted = (row.get("warehouse") == source.get("warehouse")
                         and _number(source.get("qty")) > 0)
        from_rejected = (row.get("warehouse") == source.get("rejected_warehouse")
                         and _number(source.get("rejected_qty")) > 0)
        if from_accepted == from_rejected:
            frappe.throw("委外退回仓库不能唯一对应原收回的接收或拒收实物数量")
        if from_accepted:
            accepted_qty = -qty * factor
        else:
            rejected_qty += -qty * factor
    if rejected and (not source.get("rejected_warehouse")
                     or row.get("rejected_warehouse") != source.get("rejected_warehouse")):
        frappe.throw("委外退拒收仓库与原收回明细不一致")
    return accepted_qty, rejected_qty


def _limits(source):
    result = {}
    for row in source.items:
        factor = _factor(row)
        accepted, rejected, loss = (_number(row.get(field))
                                    for field in ("qty", "rejected_qty", "process_loss_qty"))
        if min(accepted, rejected, loss) < 0:
            frappe.throw("原委外收回实物数量或损耗异常，请先核对来源")
        result[row.name] = (accepted * factor, rejected * factor)
    return result


def _prior_returns(source, current_name=None):
    # Current read, not a potentially stale REPEATABLE READ snapshot. Include
    # hidden submitted returns to bound capacity without disclosing their names.
    return frappe.db.sql("""
        select child.*, parent.company as return_company,
               parent.supplier as return_supplier,
               parent.supplier_warehouse as return_supplier_warehouse
        from `tabSubcontracting Receipt` parent
        inner join `tabSubcontracting Receipt Item` child on child.parent = parent.name
        where parent.docstatus = 1 and parent.is_return = 1
          and parent.return_against = %s and parent.name != %s
          and child.parenttype = 'Subcontracting Receipt'
        order by parent.name, child.idx
        for update
    """, (source.name, current_name or ""), as_dict=True)


def _totals(source, prior, current=()):
    source_rows = {row.name: row for row in source.items}
    totals = {name: [Decimal(0), Decimal(0)] for name in source_rows}
    for row in prior:
        if (row.return_company != source.company or row.return_supplier != source.supplier
                or row.return_supplier_warehouse != source.get("supplier_warehouse")):
            frappe.throw("既有委外退回与原收回的公司、供应商或供应仓不一致，请先核对")
    for row in [*prior, *current]:
        original = source_rows.get(row.get("subcontracting_receipt_item"))
        if not original:
            frappe.throw("委外退回必须关联准确的原收回明细，不能按同名商品猜测额度")
        accepted, rejected = _physical_pools(row, original)
        totals[original.name][0] += accepted
        totals[original.name][1] += rejected
    for name, limits in _limits(source).items():
        for total, limit in zip(totals[name], limits):
            if flt(total, _precision(source_rows[name])) > flt(limit, _precision(source_rows[name])):
                frappe.throw("委外累计实物退回超过原收回明细的剩余接收或拒收数量")
    return totals


def validate_physical_return(document, source=None, prior=None):
    source = source or _source(document.get("return_against"), document)
    prior = _prior_returns(source, document.get("name")) if prior is None else prior
    totals = _totals(source, prior, document.items)
    if any(not _number(row.get("qty")) and not _number(row.get("rejected_qty"))
           for row in document.items):
        frappe.throw("请删除不退回实物的零数量明细，再保存委外退回")
    if not any(_number(row.get("qty")) < 0 or _number(row.get("rejected_qty")) < 0
               for row in document.items):
        frappe.throw("委外退回至少需要一行负数实物数量")
    return source, prior, totals


def _material_facts(source):
    """Historical consumption by exact original FG row, in raw stock units."""
    rows = {row.name: row for row in source.items}
    facts = {name: {} for name in rows}
    for raw in source.get("supplied_items") or []:
        original = rows.get(raw.get("reference_name"))
        if not original or raw.get("main_item_code") != original.get("item_code"):
            frappe.throw("原委外收回耗料缺少准确的商品明细来源，请先核对")
        qty = _number(raw.get("consumed_qty"))
        if qty < 0 or not raw.get("rm_item_code") or not raw.get("stock_uom"):
            frappe.throw("原委外收回耗料的物料、单位或库存数量异常")
        if not qty:
            continue
        if raw.get("rate") in (None, "") or raw.get("amount") in (None, ""):
            frappe.throw("原委外收回耗料缺少已过账估值，不能按零成本退回")
        amount = _number(raw.amount)
        if amount < 0 or not _same(amount, qty * _number(raw.rate), raw, "amount"):
            frappe.throw("原委外收回耗料金额与库存数量不一致，请先核对")
        key = (raw.rm_item_code, raw.stock_uom)
        totals = facts[original.name].setdefault(key, [Decimal(0), Decimal(0)])
        totals[0] += qty
        totals[1] += amount
    for name, original in rows.items():
        if not _number(original.get("qty")) and _number(original.get("rejected_qty")) > 0:
            # Native v16.36 skips primary material assignment when accepted
            # qty is zero. Prove the exact original consumption from active
            # supplier-warehouse SLE instead of trusting that zero/stale cache.
            ledger = _source_stock_entries(source)
            for raw in source.get("supplied_items") or []:
                if raw.get("reference_name") != name:
                    continue
                selected = [entry for entry in ledger if entry.voucher_detail_no == raw.name
                            and entry.item_code == raw.rm_item_code
                            and entry.warehouse == source.supplier_warehouse]
                quantity = -sum((_number(entry.actual_qty) for entry in selected), Decimal(0))
                value = -sum((_number(entry.stock_value_difference) for entry in selected), Decimal(0))
                if (any(_number(entry.actual_qty) > 0 for entry in selected)
                        or not _same(quantity, _number(raw.consumed_qty), raw)
                        or value < 0 or not _same(value, _number(raw.amount), raw, "amount")):
                    frappe.throw("原全部拒收材料的有效库存数量或价值与准确耗料不一致")
            continue
        if original.get("rm_supp_cost") in (None, ""):
            frappe.throw("原委外收回缺少已过账材料金额，不能按零成本退回")
        amount = sum((value[1] for value in facts[name].values()), Decimal(0))
        if not _same(amount, _number(original.rm_supp_cost), original, "rm_supp_cost"):
            frappe.throw("原委外收回材料金额与准确耗料来源不一致，请先核对")
    return facts


def validate_return_cost_sources(document, source):
    """Do not let the current BOM or valuation invent historical return cost.

Native SCR calculates a return's consumed materials from today's BOM. The
physical return reverses only its proportional part of the exact original
consumption, keeping confirmed loss on the original receipt. Reject an unsafe
native result before stock/GL posting; no invoice is automatically credited.
"""
    originals = {row.name: row for row in source.items}
    facts = _material_facts(source)
    current = {row.name: row for row in document.items}
    expected = {}
    for row in document.items:
        original = originals[row.subcontracting_receipt_item]
        physical = sum(_physical_pools(row, original))
        source_qty = _covered_stock(original)
        if not source_qty:
            frappe.throw("原收回数量不足以核对退回成本，请先核对")
        ratio = physical / source_qty
        expected[row.name] = {key: [qty * ratio, amount * ratio]
                              for key, (qty, amount) in facts[original.name].items()}
        for field in ("service_cost_per_qty", "additional_cost_per_qty"):
            if original.get(field) in (None, "") or row.get(field) in (None, ""):
                frappe.throw("委外退回缺少准确的原加工或附加费用来源")
            original_rate = _number(original.get(field)) / _factor(original)
            current_rate = _number(row.get(field)) / _factor(row)
            if min(original_rate, current_rate) < 0 or not _same(original_rate, current_rate, row):
                frappe.throw("委外退回加工或附加工价与原收回不一致，不能按当前费用改写历史")
    actual = {name: {} for name in current}
    for raw in document.get("supplied_items") or []:
        row = current.get(raw.get("reference_name"))
        if not row or raw.get("main_item_code") != row.get("item_code"):
            frappe.throw("委外退回耗料无法对应准确的收回明细")
        qty = -_number(raw.get("consumed_qty"))
        if qty < 0 or not raw.get("rm_item_code") or not raw.get("stock_uom"):
            frappe.throw("委外退回耗料的物料、单位或库存数量异常")
        if not qty:
            continue
        if raw.get("rate") in (None, "") or raw.get("amount") in (None, ""):
            frappe.throw("委外退回耗料缺少原估值，不能按零成本提交")
        amount = -_number(raw.amount)
        if amount < 0 or not _same(amount, qty * _number(raw.rate), raw, "amount"):
            frappe.throw("委外退回耗料金额与库存数量不一致")
        key = (raw.rm_item_code, raw.stock_uom)
        totals = actual[row.name].setdefault(key, [Decimal(0), Decimal(0)])
        totals[0] += qty
        totals[1] += amount
    for name, values in expected.items():
        if set(values) != set(actual[name]):
            frappe.throw("当前BOM退料与原收回实际耗料不同，请按原耗料来源处理，不能猜测返料")
        row = current[name]
        for key, (qty, amount) in values.items():
            got_qty, got_amount = actual[name][key]
            if not _same(qty, got_qty, row) or not _same(amount, got_amount, row, "amount"):
                frappe.throw("当前BOM或估值退料与原收回实际耗料比例不一致，不能提交错误库存或成本")


def _source_stock_entries(source):
    cached = getattr(source, "_jifeng_return_stock_entries", None)
    if cached is not None:
        return cached
    if not frappe.has_permission("Stock Ledger Entry", "read"):
        frappe.throw("没有核对原收回实物入库价值的权限", frappe.PermissionError)
    fields = {"name", "company", "item_code", "warehouse", "voucher_type", "voucher_no",
              "voucher_detail_no", "actual_qty", "stock_value_difference", "is_cancelled"}
    _read_fields("Stock Ledger Entry", fields)
    if frappe.db.exists("Repost Item Valuation", {"company": source.company, "docstatus": 1,
            "voucher_type": RECEIPT, "voucher_no": source.name,
            "status": ["in", ["Queued", "In Progress", "Failed"]]}):
        frappe.throw("原收回库存重估尚未完成或已失败，暂不能按未稳定价值退回")
    rows = frappe.get_list("Stock Ledger Entry", filters={"company": source.company,
        "voucher_type": RECEIPT, "voucher_no": source.name, "is_cancelled": 0},
        fields=sorted(fields), limit_page_length=0)
    membership = frappe.get_all("Stock Ledger Entry", filters={"company": source.company,
        "voucher_type": RECEIPT, "voucher_no": source.name, "is_cancelled": 0},
        pluck="name", limit_page_length=0)
    names = [row.name for row in rows]
    if len(set(names)) != len(names) or set(names) != set(membership):
        frappe.throw("没有完整核对原收回实物或材料库存来源的权限", frappe.PermissionError)
    expected = {(original.name, original.item_code, warehouse)
                for original in source.items
                for field, warehouse in (("qty", original.get("warehouse")),
                                          ("rejected_qty", original.get("rejected_warehouse")))
                if _number(original.get(field))}
    expected.update((raw.name, raw.rm_item_code, source.supplier_warehouse)
                    for raw in source.get("supplied_items") or [] if _number(raw.get("consumed_qty")))
    if any((row.voucher_detail_no, row.item_code, row.warehouse) not in expected for row in rows):
        frappe.throw("原收回有效库存账存在无法对应准确实物或耗料的明细，请先核对")
    source._jifeng_return_stock_entries = rows
    return rows


def _stock_pools(source):
    cached = getattr(source, "_jifeng_return_stock_pools", None)
    if cached is not None:
        return cached
    rows = _source_stock_entries(source)
    pools = {}
    for original in source.items:
        values = []
        for field, warehouse in (("qty", original.get("warehouse")),
                                 ("rejected_qty", original.get("rejected_warehouse"))):
            quantity = _number(original.get(field)) * _factor(original)
            selected = [row for row in rows if row.voucher_detail_no == original.name
                        and row.item_code == original.item_code and row.warehouse == warehouse]
            actual = sum((_number(row.actual_qty) for row in selected), Decimal(0))
            value = sum((_number(row.stock_value_difference) for row in selected), Decimal(0))
            if (any(_number(row.actual_qty) < 0 for row in selected)
                    or not _same(actual, quantity, original) or value < 0):
                frappe.throw("原收回准确实物库存来源不完整或价值异常，不能猜测退回单价")
            values.append(value / quantity if quantity else Decimal(0))
        pools[original.name] = tuple(values)
    source._jifeng_return_stock_pools = pools
    return pools


_COPY_EXCLUDE = {"doctype", "name", "parent", "parenttype", "parentfield", "idx", "docstatus",
                 "creation", "modified", "owner", "modified_by", "__islocal", "__unsaved"}


def _copy_row(row):
    return {field: value for field, value in row.as_dict().items() if field not in _COPY_EXCLUDE}


def _tracking(raw, allow_draft=False, company=None, warehouse=None):
    serials = [value.strip() for value in (raw.get("serial_no") or "").splitlines() if value.strip()]
    batches = {raw.get("batch_no")} if raw.get("batch_no") else set()
    if raw.get("serial_and_batch_bundle"):
        bundle = frappe.get_doc("Serial and Batch Bundle", raw.serial_and_batch_bundle)
        bundle.check_permission("read")
        if (bundle.get("is_cancelled") or bundle.get("item_code") != raw.get("rm_item_code")
                or bundle.get("docstatus") not in ({0, 1} if allow_draft else {1})
                or bundle.get("voucher_type") != RECEIPT
                or bundle.get("voucher_no") != raw.get("parent")
                or company and bundle.get("company") != company
                or warehouse and bundle.get("warehouse") != warehouse):
            frappe.throw("原收回材料批次或序列号来源无效，请先核对")
        _read_fields("Serial and Batch Bundle", {"entries", "item_code", "company", "warehouse",
                                                "voucher_type", "voucher_no", "docstatus", "is_cancelled"})
        _read_fields("Serial and Batch Entry", {"serial_no", "batch_no", "qty"}, "Serial and Batch Bundle")
        serials = [entry.serial_no for entry in bundle.get("entries") or [] if entry.get("serial_no")]
        batches = {entry.batch_no for entry in bundle.get("entries") or [] if entry.get("batch_no")}
    if len(serials) != len(set(serials)):
        frappe.throw("原收回材料序列号重复，不能猜测实物退回来源")
    if serials and Decimal(len(serials)) != abs(_number(raw.get("consumed_qty"))):
        frappe.throw("原收回材料序列号数量与准确库存数量不一致")
    return serials, batches


def _prior_raw_returns(source, current_name):
    return frappe.db.sql("""
        select raw.*, fg.subcontracting_receipt_item as source_fg_row
        from `tabSubcontracting Receipt` parent
        inner join `tabSubcontracting Receipt Supplied Item` raw on raw.parent = parent.name
        inner join `tabSubcontracting Receipt Item` fg
          on fg.name = raw.reference_name and fg.parent = parent.name
        where parent.docstatus = 1 and parent.is_return = 1
          and parent.return_against = %s and parent.name != %s
          and raw.parenttype = 'Subcontracting Receipt'
          and fg.parenttype = 'Subcontracting Receipt'
        order by parent.name, raw.idx
        for update
    """, (source.name, current_name or ""), as_dict=True)


def _return_source(document):
    # The source is locked on each native calculation request, including native
    # form recalculation requests made before Save. No cached historical clone.
    source = getattr(document, "_jifeng_return_source", None)
    return source or _source(document.get("return_against"), document)


def _historical_materials(document, source):
    originals = {row.name: row for row in source.items}
    _material_facts(source)
    current = list(document.get("supplied_items") or [])
    prior = _prior_raw_returns(source, document.get("name"))
    used_serials = {}
    for raw in prior:
        serials, _ = _tracking(raw, company=document.company, warehouse=source.get("supplier_warehouse"))
        key = (raw.source_fg_row, raw.rm_item_code, raw.stock_uom)
        used_serials.setdefault(key, set()).update(serials)
    values = []
    for row in document.items:
        original = originals[row.subcontracting_receipt_item]
        covered = _covered_stock(original)
        ratio = sum(_physical_pools(row, original)) / covered
        for raw in source.get("supplied_items") or []:
            if raw.get("reference_name") != original.name or not _number(raw.get("consumed_qty")):
                continue
            qty = _number(raw.consumed_qty) * ratio
            copied = _copy_row(raw)
            copied.update(reference_name=row.name, main_item_code=row.item_code,
                          subcontracting_order=row.subcontracting_order,
                          consumed_qty=-float(qty), required_qty=-float(qty),
                          amount=-float(_number(raw.amount) * ratio),
                          conversion_factor=float(_factor(row)), serial_and_batch_bundle=None)
            serials, batches = _tracking(raw, company=document.company, warehouse=source.get("supplier_warehouse"))
            if len(batches) > 1:
                frappe.throw("该原耗料行含多个批次，须按原批次份额明确退回，当前不能猜测分批数量")
            key = (original.name, raw.rm_item_code, raw.stock_uom)
            selected = next((old for old in current if old.get("reference_name") == row.name
                             and old.get("rm_item_code") == raw.rm_item_code
                             and old.get("stock_uom") == raw.stock_uom
                             and old.get("bom_detail_no") == raw.get("bom_detail_no")
                             and old.get("batch_no") == raw.get("batch_no")), None)
            if selected:
                current.remove(selected)
                copied["name"] = selected.name
            if serials:
                count = int(qty)
                if qty != count:
                    frappe.throw("序列号材料退回份额不是整数，须明确原序列号实物数量")
                used = used_serials.setdefault(key, set())
                allowed = set(serials) - used
                chosen = _tracking(selected, allow_draft=True, company=document.company,
                                   warehouse=source.get("supplier_warehouse"))[0] if selected else []
                if chosen and (len(chosen) != count or not set(chosen).issubset(allowed)):
                    frappe.throw("本次材料序列号数量或来源与原收回剩余实物不一致")
                chosen = chosen or [serial for serial in serials if serial in allowed][:count]
                if len(chosen) != count:
                    frappe.throw("原收回材料剩余序列号不足，不能重复退回实物")
                copied["serial_no"] = "\n".join(chosen)
                used.update(chosen)
            copied["batch_no"] = next(iter(batches)) if batches else ""
            copied["use_serial_batch_fields"] = 1 if serials or batches else 0
            values.append(copied)
    document.set("supplied_items", [])
    for value in values:
        document.append("supplied_items", value)


class SubcontractingReceiptReturnMixin:
    def validate_items(self):
        if not cint(self.get("is_return")):
            return super().validate_items()
        # A changed/inactive BOM must not replace or block an exact historical
        # return. Keep native stock/subcontracted Item checks; only replace the
        # current BOM checks/calculation with the exact posted receipt facts.
        validate_physical_return(self)
        for row in self.items:
            if not frappe.get_cached_value("Item", row.item_code, "is_stock_item"):
                frappe.throw("委外实物退回商品必须为库存物料")
            if not frappe.get_cached_value("Item", row.item_code, "is_sub_contracted_item"):
                frappe.throw("委外实物退回商品必须为委外加工物料")

    def reset_supplied_items(self):
        if not cint(self.get("is_return")):
            return super().reset_supplied_items()

    def create_raw_materials_supplied_or_received(self, raw_material_table="supplied_items"):
        if not cint(self.get("is_return")):
            return super().create_raw_materials_supplied_or_received(raw_material_table=raw_material_table)
        if raw_material_table != "supplied_items":
            frappe.throw("委外实物退回材料表来源无效")
        source = _return_source(self)
        self.initialized_fields()
        self.raw_material_table = raw_material_table
        self.subcontract_orders = sorted({row.subcontracting_order for row in self.items
                                         if row.get("subcontracting_order")})
        _historical_materials(self, source)

    def set_valuation_rate_for_rm(self):
        if not cint(self.get("is_return")):
            return super().set_valuation_rate_for_rm()
        # Historical material rows already hold their original posted rates.
        # Today's supplier warehouse rate must not rewrite the return.
        self.calculate_items_qty_and_amount()

    def calculate_additional_costs(self):
        if not cint(self.get("is_return")):
            if (any(not _number(row.get("qty")) and _number(row.get("rejected_qty")) > 0
                    for row in self.items)
                    and any(_number(row.get("amount")) for row in self.get("additional_costs") or [])):
                frappe.throw("全部拒收收回的附加费用分摊规则尚未明确，请先核对费用分摊，不能按零合格数量计算")
            return super().calculate_additional_costs()
        source = _return_source(self)
        originals = {row.name: row for row in source.items}
        total = Decimal(0)
        for row in self.items:
            original = originals[row.subcontracting_receipt_item]
            for field in ("service_cost_per_qty", "additional_cost_per_qty"):
                if original.get(field) in (None, ""):
                    frappe.throw("原收回缺少已过账加工或附加费用，不能按零补齐")
                row.set(field, float(_number(original.get(field)) / _factor(original) * _factor(row)))
            total += _number(row.qty) * _number(row.additional_cost_per_qty)
        charges = source.get("additional_costs") or []
        original_total = sum((_number(charge.get("amount")) for charge in charges), Decimal(0))
        if total and not original_total:
            frappe.throw("原收回附加费用缺少准确的费用科目来源")
        ratio = -total / original_total if original_total else Decimal(0)
        self.set("additional_costs", [])
        for charge in charges:
            value = _copy_row(charge)
            for field in ("amount", "base_amount"):
                value[field] = -float(_number(charge.get(field)) * ratio)
            self.append("additional_costs", value)
        self.total_additional_costs = float(total)

    def calculate_items_qty_and_amount(self):
        if not cint(self.get("is_return")):
            self._set_all_rejected_material_costs()
            return super().calculate_items_qty_and_amount()
        source = _return_source(self)
        originals = {row.name: row for row in source.items}
        stock_pools = _stock_pools(source)
        material_costs = {}
        for raw in self.get("supplied_items") or []:
            raw.amount = float(_number(raw.consumed_qty) * _number(raw.rate))
            material_costs[raw.reference_name] = material_costs.get(raw.reference_name, Decimal(0)) + _number(raw.amount)
        total_qty, total_amount = Decimal(0), Decimal(0)
        for row in self.items:
            original = originals[row.subcontracting_receipt_item]
            if (original.get("secondary_item_type") or original.get("valuation_type")
                    or _number(original.get("secondary_items_cost_per_qty"))
                    or _number(original.get("landed_cost_voucher_amount"))):
                frappe.throw("原收回含副产品估值或到岸成本，须先明确这些原过账份额，不能猜测退回成本")
            row.received_qty = float(_number(row.qty) + _number(row.get("rejected_qty")))
            if not _number(row.received_qty):
                frappe.throw("请删除不退回实物的零数量明细，再保存委外退回")
            row.rm_supp_cost = float(material_costs.get(row.name, Decimal(0)))
            row.rm_cost_per_qty = float(_number(row.rm_supp_cost) / _number(row.received_qty))
            for field in ("service_cost_per_qty", "additional_cost_per_qty"):
                if original.get(field) in (None, ""):
                    frappe.throw("原收回缺少已过账加工或附加费用，不能按零补齐")
                row.set(field, float(_number(original.get(field)) / _factor(original) * _factor(row)))
            # Finished stock value and restored material/fee costs have distinct
            # bases when the original included loss/rejection. Preserve the
            # exact original accepted/rejected SLE pool; native GL accounts for
            # the difference. Never divide accepted unit value by covered qty.
            accepted, rejected = _physical_pools(row, original)
            accepted_rate, rejected_rate = stock_pools[original.name]
            row.amount = -float(accepted * accepted_rate + rejected * rejected_rate)
            if accepted and rejected and not _same(accepted_rate, rejected_rate, row, "rate"):
                frappe.throw("接收和拒收原入库单价不同，请分开两行退回实物")
            row.rate = float(accepted_rate if accepted else rejected_rate)
            total_qty += _number(row.qty) + _number(row.get("rejected_qty"))
            total_amount += _number(row.amount)
        self.total_qty, self.total = float(total_qty), float(total_amount)

    def _set_all_rejected_material_costs(self):
        """Repair the native zero-accepted branch before its own calculation.

        This runs on normal set_missing_values and submit validation; native
        stock/GL writers and cancellation remain unchanged. New receipts use
        native generated consumption/current valuation, not historical guesses.
        """
        for row in self.items:
            if (row.get("secondary_item_type") or row.get("valuation_type")
                    or _number(row.get("qty")) or _number(row.get("rejected_qty")) <= 0):
                continue
            if (not row.get("bom") or _number(row.get("landed_cost_voucher_amount"))
                    or any(item.get("reference_name") == row.name
                           and (item.get("secondary_item_type") or item.get("valuation_type"))
                           for item in self.items)):
                frappe.throw("全部拒收含副产品、无BOM或到岸成本，尚不能准确计算原生收回成本")
            # Native writes rejected SLE inside `if item.warehouse`, although
            # its accepted-warehouse validator skips accepted qty == 0.
            # Apply the same explicit header default, never lose rejected FG
            # while posting only supplier-warehouse consumption.
            if not row.get("warehouse"):
                row.warehouse = self.get("set_warehouse")
            if not row.get("warehouse"):
                frappe.throw("全部拒收收回仍须填写接收仓库和拒收仓库，请补齐后保存")
            received = sum((_number(row.get(field)) for field in
                            ("qty", "rejected_qty", "process_loss_qty")), Decimal(0))
            if received <= 0 or _number(row.get("process_loss_qty")) < 0:
                frappe.throw("全部拒收的覆盖数量或损耗无效")
            material = Decimal(0)
            for raw in self.get("supplied_items") or []:
                if raw.get("reference_name") != row.name:
                    continue
                if (raw.get("main_item_code") != row.get("item_code")
                        or raw.get("consumed_qty") in (None, "") or raw.get("rate") in (None, "")
                        or min(_number(raw.consumed_qty), _number(raw.rate)) < 0):
                    frappe.throw("全部拒收的准确耗料数量或估值缺失")
                material += _number(raw.consumed_qty) * _number(raw.rate)
            row.received_qty = float(received)
            row.rm_supp_cost = float(material)
            row.rm_cost_per_qty = float(material / received)
            row.secondary_items_cost_per_qty = 0

    @frappe.whitelist()
    def get_secondary_items(self, recalculate_rate=False):
        if not cint(self.get("is_return")):
            return super().get_secondary_items(recalculate_rate=recalculate_rate)
        self.calculate_items_qty_and_amount()

    def validate_bom_required_qty(self):
        if not cint(self.get("is_return")):
            return super().validate_bom_required_qty()
        validate_return_cost_sources(self, _return_source(self))

    def validate(self):
        if not cint(self.get("is_return")) or self.docstatus == 2:
            return super().validate()
        source, prior, totals = validate_physical_return(self)
        # Native calculation can adjust qty/received_qty. It must not silently
        # change the operator's selected physical pools or restore confirmed loss.
        self._jifeng_return_source = source
        try:
            result = super().validate()
        finally:
            self._jifeng_return_source = None
        _, _, calculated = validate_physical_return(self, source=source, prior=prior)
        for name, values in totals.items():
            original = next(row for row in source.items if row.name == name)
            if any(not _same(a, b, original) for a, b in zip(values, calculated[name])):
                frappe.throw("委外原生计算改变了实物退回数量，请核对原收回明细后保存")
        validate_return_cost_sources(self, source)
        return result


def _make_return(source_name, target_doc=None, rejected=False):
    from erpnext.controllers.sales_and_purchase_return import make_return_doc

    if not frappe.has_permission(RECEIPT, "create"):
        frappe.throw("没有创建委外退回凭证的权限", frappe.PermissionError)
    source = _source(source_name)
    prior = _prior_returns(source)
    totals = _totals(source, prior)
    limits = _limits(source)
    # Native mapping preserves its stock/account/serial bundle/print defaults.
    # This only prepares a draft; it never saves, submits or credits an invoice.
    target = make_return_doc(RECEIPT, source_name, target_doc,
                             return_against_rejected_qty=rejected)
    rows = {row.name: row for row in source.items}
    kept = []
    for row in target.items:
        original = rows.get(row.get("subcontracting_receipt_item"))
        if not original:
            frappe.throw("原生委外退回映射缺少准确的收回明细")
        pool = 1 if rejected else 0
        remaining = limits[original.name][pool] - totals[original.name][pool]
        if flt(remaining, _precision(original)) <= 0:
            continue
        row.qty = -float(remaining / _factor(row))
        row.rejected_qty = 0
        row.process_loss_qty = 0
        row.received_qty = row.qty
        row.warehouse = original.get("rejected_warehouse") if rejected else original.get("warehouse")
        row.rejected_warehouse = "" if rejected else original.get("rejected_warehouse")
        # Custom flow links and native stage references must survive mapping.
        for field in _ROW_IDENTITY:
            if field != "item_code":
                row.set(field, original.get(field))
        row.idx = len(kept) + 1
        kept.append(row)
    target.set("items", kept)
    if not kept:
        frappe.throw("所选原收回的接收或拒收实物已全部退回")
    validate_physical_return(target, source=source, prior=prior)
    target._jifeng_return_source = source
    try:
        target.create_raw_materials_supplied_or_received()
        target.calculate_additional_costs()
        target.calculate_items_qty_and_amount()
    finally:
        target._jifeng_return_source = None
    validate_return_cost_sources(target, source)
    return target


@frappe.whitelist(methods=["POST"])
def make_subcontract_return(source_name, target_doc=None):
    return _make_return(source_name, target_doc=target_doc)


@frappe.whitelist(methods=["POST"])
def make_subcontract_return_against_rejected_warehouse(source_name):
    return _make_return(source_name, rejected=True)
