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


GL_FEE_FIELDS = {"name", "modified", "company", "voucher_type", "voucher_no", "voucher_detail_no",
                 "account", "cost_center", "project", "debit", "credit", "is_cancelled"}


def receipt_accounting_dimensions():
    from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_accounting_dimensions
    return tuple(get_accounting_dimensions())


def receipt_gl_proof_fields():
    from erpnext.accounts.general_ledger import get_merge_properties
    meta = frappe.get_meta("GL Entry")
    columns = set(meta.get_valid_columns())
    return GL_FEE_FIELDS | {field for field in get_merge_properties(receipt_accounting_dimensions())
        if field in columns and not (meta.get_field(field) and meta.get_field(field).get("is_virtual"))}


def _booked_receipt_fee(row, field):
    if field == "service_cost_per_qty":
        return (_number(flt(row.get(field), _precision(row, field)))
                * _number(flt(row.get("qty"), _precision(row, "qty"))))
    return _number(row.get("qty")) * _number(row.get(field))


class _ReceiptFeeProof:
    """Server-local proof capability; client flags cannot stand in for it."""
    def __init__(self, document, *, reversible, unallocated):
        self.document = document
        self.reversible = reversible
        self.unallocated = unallocated
        self.rows = {row.name: (tuple(row.get(field) for field in
            ("qty", "conversion_factor", "service_cost_per_qty", "additional_cost_per_qty")),
            {field: _booked_receipt_fee(row, field) for field in
             ("service_cost_per_qty", "additional_cost_per_qty")})
            for row in document.items} if not cint(document.get("is_return")) else {}

    def amount(self, original, field):
        saved = self.rows.get(original.name)
        if not saved or saved[0] != tuple(original.get(field) for field in
                ("qty", "conversion_factor", "service_cost_per_qty", "additional_cost_per_qty")):
            frappe.throw("原费用证明与准确已保存商品明细不一致")
        return saved[1][field]


def _receipt_fee_ledger(document):
    # Native stock submit has no general GL-read requirement. This private
    # proof reads only the already readable receipt's exact company/voucher;
    # it discloses no GL fields. Cost readers pass their permission-checked
    # Session reader instead and retain full field/mask/closure checks.
    document.check_permission("read")
    _read_source_fields(document)
    filters = {"company": document.company, "voucher_type": RECEIPT,
               "voucher_no": document.name, "is_cancelled": 0}
    fields = receipt_gl_proof_fields()
    rows = frappe.get_all("GL Entry", filters=filters, fields=sorted(fields), limit_page_length=0)
    names = [row.name for row in rows]
    if (len(set(names)) != len(names)
            or any(not fields.issubset(row) for row in rows)):
        frappe.throw("无法完整核对原收回有效会计费用来源")
    return rows


class _LegacyReceiptGLRow:
    """Read-only native calculation view; never mutate the original Document."""
    def __init__(self, document, row):
        self._document, self._row = document, row
        self.items, self.supplied_items, self.additional_costs = [row], document.get("supplied_items") or [], []

    def __getattr__(self, name):
        return getattr(self._document, name)

    def get(self, name, default=None):
        return getattr(self, name) if name in ("items", "supplied_items", "additional_costs") else self._document.get(name, default)

    def add_gl_entry(self, **values):
        values["voucher_detail_no"] = self._row.name
        self._document.add_gl_entry(**values)


def _prove_receipt_fees(document, *, ledger_reader=None, for_return=False, _source_proof=None):
    """Prove each complete FG/charge contribution against effective native GL.

    New postings retain the native voucher_detail_no merge identity. Legacy
    merged entries are usable only when nonzero row groups have disjoint
    merge identities, so their actual amounts can be uniquely attributed.
    """
    primary = [row for row in document.items if not row.get("secondary_item_type") and not row.get("valuation_type")]
    if (not primary or len(primary) != len(document.items) or len({row.name for row in primary}) != len(primary)
            or any(row.get("landed_cost_voucher_amount") for row in document.items)):
        frappe.throw("原收回费用缺少可逐行核实的有效会计归属，不能按整单合计猜测退回份额")
    actual = (ledger_reader or _receipt_fee_ledger)(document)
    from erpnext.subcontracting.doctype.subcontracting_receipt.subcontracting_receipt import SubcontractingReceipt
    from erpnext.accounts.general_ledger import get_merge_properties, process_gl_map
    fields = receipt_gl_proof_fields()
    if any(not fields.issubset(entry) for entry in actual):
        frappe.throw("没有完整读取逐行费用会计归属或会计维度的权限", frappe.PermissionError)
    if ledger_reader is None:
        _source_stock_entries(document)
    account_map = document.get_inventory_account_map()
    expected = []
    for row in primary:
        # Capture each actual old native contribution using a read-only view.
        # Its accepted qty/rate gate remains evidence of the legacy posting.
        if _number(row.get("qty")) and _number(row.get("rate")):
            SubcontractingReceipt.make_item_gl_entries(_LegacyReceiptGLRow(document, row), expected, account_map)
    corrected = []
    if cint(document.get("is_return")) and _source_proof is None:
        source = _source(document.get("return_against"), document)
        _source_proof = _prove_receipt_fees(source, ledger_reader=ledger_reader, for_return=True)
    SubcontractingReceiptReturnMixin.make_item_gl_entries(document, corrected, account_map,
                                                        _source_proof=_source_proof)
    charge_ids = {charge.name for charge in document.get("additional_costs") or []}
    expected.extend(entry for entry in corrected if entry.get("voucher_detail_no") in charge_ids)
    from copy import deepcopy
    unallocated = deepcopy(corrected)
    merge_fields = tuple(get_merge_properties(receipt_accounting_dimensions()))
    transient = set(merge_fields) - fields
    def check_transient(entries):
        if any(entry.get(field) not in (None, "") for entry in entries for field in transient):
            frappe.throw("原收回会计使用非持久化合并标识，无法从实际会计账完整证明归属")
    # The native merge key also contains temporary advance identities that
    # are not database columns. They may be omitted only when truly empty.
    check_transient([*expected, *corrected])
    # Native allocation keeps row identities; from_repost avoids budget side
    # effects in this proof while preserving allocation, merge and sign rules.
    expected = process_gl_map(expected, from_repost=True)
    corrected = process_gl_map(corrected, from_repost=True)
    check_transient([*expected, *corrected])
    precision = _precision(primary[0], "amount")

    def totals(entries, *, include_detail=True):
        values = {}
        for entry in entries:
            key = tuple(entry.get(field) or "" for field in merge_fields
                        if include_detail or field != "voucher_detail_no")
            values[key] = values.get(key, Decimal(0)) + _number(entry.get("debit")) - _number(entry.get("credit"))
        return {key: flt(value, precision) for key, value in values.items() if flt(value, precision)}

    if any(entry.get("company") != document.company or entry.get("voucher_type") != RECEIPT
            or entry.get("voucher_no") != document.name or cint(entry.get("is_cancelled")) for entry in actual):
        frappe.throw("原收回有效会计费用凭证范围不一致")
    marked = any(entry.get("voucher_detail_no") for entry in actual)
    identities = {row.name for row in primary} | charge_ids
    if marked and any(entry.get("voucher_detail_no") not in identities for entry in actual):
        frappe.throw("原收回会计明细标识缺失或不属于准确商品及费用来源")
    actual_totals = totals(actual, include_detail=marked)
    legacy_totals = totals(expected, include_detail=marked)
    corrected_totals = totals(corrected, include_detail=marked)
    # Real free stock can have effective nonzero-quantity SLE but no monetary
    # GL after native zero-entry filtering. Exact SLE were checked above;
    # absence is a fault only if that proven source needs nonzero postings.
    if not actual and corrected_totals:
        frappe.throw("原收回没有有效会计费用来源，不能反冲尚未入账费用")
    if (actual_totals not in (legacy_totals, corrected_totals)
            or flt(sum((_number(value) for value in actual_totals.values()), Decimal(0)), precision)):
        frappe.throw("原收回有效会计分录与准确库存、耗料和已保存费用不一致，不能猜测费用退回")
    if (for_return or cint(document.get("is_return"))) and actual_totals != corrected_totals:
        frappe.throw("原收回历史会计差额与当前退回方向不一致，须先核对原账，不能生成未入账或错误反向分录")
    if not marked and len(identities) > 1:
        accepted = corrected if actual_totals == corrected_totals else expected
        owners = {}
        for identity in identities:
            row_totals = totals([entry for entry in accepted if entry.get("voucher_detail_no") == identity], include_detail=False)
            for key in row_totals:
                if key in owners:
                    frappe.throw("原收回历史合并会计费用不能逐行核实归属，不能按整单合计猜测退回份额")
                owners[key] = identity
    return _ReceiptFeeProof(document, reversible=actual_totals == corrected_totals, unallocated=unallocated)


def receipt_accounting_fee(document, row, field, original=None, *, ledger_reader=None, fee_source=None, _proof=None):
    """Native accepted-quantity fee, or its exact accepted-stock reversal.

    received_qty is the material/estimated FG cost basis. It is not the
    native booked processing/additional fee basis. A rejected-stock return
    must not reverse fees originally booked on accepted goods.
    """
    if row.get(field) in (None, ""):
        frappe.throw("收回缺少已保存费用单价，不能猜测费用入账")
    if not cint(document.get("is_return")):
        return _booked_receipt_fee(row, field)
    if original is None or original.get(field) in (None, ""):
        frappe.throw("退回缺少准确原收回费用来源")
    qty = _number(row.get("qty"))
    if not qty or _number(original.get("qty")) <= 0 or row.get("warehouse") != original.get("warehouse"):
        return Decimal(0)
    if (row.get("warehouse") == original.get("rejected_warehouse")
            and _number(original.get("rejected_qty"))):
        frappe.throw("原接受与拒收仓相同，不能明确原已入账费用的退回份额")
    accepted_stock = -qty * _factor(row)
    original_stock = _number(original.get("qty")) * _factor(original)
    if accepted_stock < 0 or accepted_stock > original_stock and not _same(accepted_stock, original_stock, original):
        frappe.throw("接受实物退回费用份额超过准确原接受数量")
    amount = _booked_receipt_fee(original, field)
    if amount:
        source = fee_source or (_proof.document if isinstance(_proof, _ReceiptFeeProof) else _return_source(document))
        if _proof is None:
            _proof = _prove_receipt_fees(source, ledger_reader=ledger_reader, for_return=True)
        if not isinstance(_proof, _ReceiptFeeProof) or _proof.document is not source or not _proof.reversible:
            frappe.throw("原收回费用须由本次服务器来源证明，不能用客户端标志跳过")
        amount = _proof.amount(original, field)
    return -amount * accepted_stock / original_stock


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
                           "docstatus", "is_return", "return_against", "project", "cost_center", "finance_book"}
                         | set(receipt_accounting_dimensions()))
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
    return _validate_source(source, document)


def _validate_source(source, document=None):
    """Check a freshly loaded source or a server-local proven source alike."""
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
          and child.parentfield = 'items'
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
    lot_values = _validate_material_lot_returns(document, source)
    finished_values = _finished_return_values(document, source)
    for row in document.items:
        value = finished_values.get(row.name)
        if value is not None and (not _same(-_number(row.get("amount")), value, row, "amount")
                or not _same(-(_number(row.get("qty")) + _number(row.get("rejected_qty")))
                             * _factor(row) * _number(row.get("rate")), value, row, "amount")):
            frappe.throw("成品退回计算金额或单价与明确选择的准确原批次价值不一致")
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
        for raw in document.get("supplied_items") or []:
            if raw.get("reference_name") == row.name and id(raw) in lot_values:
                origin = _origin_raw(source, original.name, raw)
                if not _same(-_number(raw.consumed_qty), _number(origin.consumed_qty) * ratio, raw):
                    frappe.throw("本次批次退料数量与准确原耗料实物份额不一致")
                key = (raw.rm_item_code, raw.stock_uom)
                expected[row.name][key][1] += lot_values[id(raw)] - _number(origin.amount) * ratio
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
              "voucher_detail_no", "actual_qty", "stock_value_difference", "is_cancelled", "serial_and_batch_bundle",
              "serial_no", "batch_no"}
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


BUNDLE = "Serial and Batch Bundle"
BUNDLE_ENTRY = "Serial and Batch Entry"
BUNDLE_FIELDS = {"name", "modified", "docstatus", "item_code", "company", "warehouse",
                 "voucher_type", "voucher_no", "voucher_detail_no", "type_of_transaction",
                 "total_qty", "is_cancelled"}
BUNDLE_ENTRY_FIELDS = {"name", "modified", "docstatus", "parent", "parenttype", "parentfield", "idx",
                       "serial_no", "batch_no", "qty", "incoming_rate", "stock_value_difference", "warehouse"}
FG_TRACKING_FIELDS = {"serial_and_batch_bundle", "rejected_serial_and_batch_bundle",
                      "serial_no", "rejected_serial_no", "batch_no", "use_serial_batch_fields"}


def _has_tracking(raw):
    return any(raw.get(field) for field in ("serial_and_batch_bundle", "serial_no", "batch_no"))


def _origin_raw(source, fg_name, raw):
    matches = [original for original in source.get("supplied_items") or []
               if original.get("reference_name") == fg_name
               and all((original.get(field) or "") == (raw.get(field) or "")
                       for field in ("rm_item_code", "stock_uom", "bom_detail_no"))]
    if len(matches) != 1:
        frappe.throw("退料无法唯一绑定原耗料明细，请明确原耗料来源，不能合并同名物料批次")
    return matches[0]


def _read_bundle(name):
    bundle = frappe.get_doc(BUNDLE, name)
    bundle.check_permission("read")
    _read_fields(BUNDLE, BUNDLE_FIELDS)
    meta = frappe.get_meta(BUNDLE)
    field = meta.get_field("entries")
    levels = set(meta.get_permlevel_access(permission_type="read", user=frappe.session.user))
    masks = set(meta.get_permlevel_access(permission_type="mask", user=frappe.session.user))
    if (not field or field.fieldtype != "Table" or field.options != BUNDLE_ENTRY
            or (field.permlevel or 0) not in levels
            or frappe.session.user != "Administrator" and field.get("mask") and (field.permlevel or 0) not in masks):
        frappe.throw("没有完整读取原耗料批次和序列号明细的权限", frappe.PermissionError)
    _read_fields(BUNDLE_ENTRY, BUNDLE_ENTRY_FIELDS, BUNDLE)
    return bundle


def material_lots(raw, company, warehouse, *, original=False, allow_draft=False, reader=None, bundle=None,
                  source_direction="Outward", return_direction="Inward"):
    """Persisted original lots, or the operator's explicit native selection.

    Values for original pools come from effective bundle entries. A draft's
    valuation is not trusted: the receipt and native bundle hook recompute it
    from its exact original pool before posting.
    """
    quantity = abs(_number(raw.get("consumed_qty")))
    if bundle is None and raw.get("serial_and_batch_bundle"):
        bundle = reader(BUNDLE, raw.get("serial_and_batch_bundle")) if reader else _read_bundle(raw.get("serial_and_batch_bundle"))
    pools = {}
    if bundle is not None:
        draft = allow_draft and bundle.docstatus == 0
        direction = source_direction if original else return_direction
        if (bundle.get("is_cancelled") or bundle.get("item_code") != raw.get("rm_item_code")
                or bundle.docstatus not in ({0, 1} if allow_draft else {1})
                or bundle.get("voucher_type") != RECEIPT or bundle.get("company") != company
                or bundle.get("warehouse") != warehouse
                or bundle.get("type_of_transaction") != direction
                or bundle.get("voucher_no") not in ({None, "", raw.get("parent")} if draft else {raw.get("parent")})
                or bundle.get("voucher_detail_no") not in ({None, "", raw.get("name")} if draft else {raw.get("name")})):
            frappe.throw("原耗料或本次退料批次凭证的物料、仓库、方向或明细来源不一致")
        for entry in bundle.get("entries") or []:
            qty = _number(entry.get("qty"))
            key = ("serial", entry.serial_no) if entry.get("serial_no") else ("batch", entry.get("batch_no"))
            if (not key[1] or not qty or (qty < 0) != (direction == "Outward")
                    or entry.get("docstatus") != bundle.docstatus
                    or entry.get("warehouse") not in ({None, "", warehouse} if draft else {warehouse})):
                frappe.throw("耗料批次或序列号的数量、仓库或来源缺失")
            pool = pools.setdefault(key, {"qty": Decimal(0), "value": Decimal(0), "rates": set(), "batch": entry.get("batch_no")})
            if key[0] == "serial" and (pool["qty"] or abs(qty) != 1):
                frappe.throw("耗料序列号重复或数量不是一件")
            pool["qty"] += abs(qty)
            if original or not draft:
                if entry.get("incoming_rate") in (None, "") or entry.get("stock_value_difference") in (None, ""):
                    frappe.throw("原耗料或已提交退料缺少逐批次、逐序列号历史估值")
                rate, value = _number(entry.incoming_rate), _number(entry.stock_value_difference)
                if rate < 0 or not _same(value, qty * rate, entry, "stock_value_difference"):
                    frappe.throw("耗料批次或序列号的原估值与库存金额不一致")
                pool["rates"].add(rate)
                pool["value"] += abs(value)
        if (not _same(sum((pool["qty"] for pool in pools.values()), Decimal(0)), quantity, raw)
                or not _same(abs(_number(bundle.get("total_qty"))), quantity, raw)):
            frappe.throw("批次凭证数量与该原耗料或本次退料明细不一致，请重新明确选择")
        if original and not _same(sum((pool["value"] for pool in pools.values()), Decimal(0)), _number(raw.get("amount")), raw, "amount"):
            frappe.throw("原批次凭证估值与该原耗料金额不一致，不能猜测原单价")
    else:
        serials = [value.strip() for value in (raw.get("serial_no") or "").splitlines() if value.strip()]
        if len(set(serials)) != len(serials) or serials and Decimal(len(serials)) != quantity:
            frappe.throw("明确选择的序列号数量与本次实物退料不一致")
        if original and len(serials) > 1:
            frappe.throw("原序列号耗料缺少逐号历史估值凭证，不能用平均价猜测部分退回")
        keys = [("serial", serial) for serial in serials] or ([("batch", raw.get("batch_no"))] if raw.get("batch_no") else [])
        for key in keys:
            qty = Decimal(1) if key[0] == "serial" else quantity
            rate = _number(raw.get("rate"))
            pools[key] = {"qty": qty, "value": qty * rate, "rates": {rate}, "batch": raw.get("batch_no")}
    return pools


def selected_material_value(original, returned, company, warehouse, *, reader=None, bundle=None,
                            used=None, allow_draft=False, source_direction="Outward", return_direction="Inward"):
    if _number(original.get("consumed_qty")) <= 0 or _number(returned.get("consumed_qty")) >= 0:
        frappe.throw("批次退料须对应原正数耗料和本次负数实物数量")
    source = material_lots(original, company, warehouse, original=True, reader=reader,
                           source_direction=source_direction, return_direction=return_direction)
    chosen = material_lots(returned, company, warehouse, allow_draft=allow_draft, reader=reader, bundle=bundle,
                           source_direction=source_direction, return_direction=return_direction)
    if not source or not chosen:
        frappe.throw("请在原生批次或序列号选择器明确选择本次退回实物，不能自动选批")
    used = used if used is not None else {}
    value = Decimal(0)
    for key, current in chosen.items():
        pool = source.get(key)
        if not pool or (current["batch"] or "") != (pool["batch"] or ""):
            frappe.throw("选择的批次或序列号不属于准确原耗料明细")
        total = used.get(key, Decimal(0)) + current["qty"]
        if total > pool["qty"] and not _same(total, pool["qty"], original):
            frappe.throw("该原耗料批次或序列号累计退回超过原数量，不能重复退回")
        if len(pool["rates"]) > 1 and (used.get(key, Decimal(0)) or not _same(current["qty"], pool["qty"], original)):
            frappe.throw("同一原批次有多个历史估值层，部分退回无法唯一确定原金额")
        rate = pool["value"] / pool["qty"]
        if current["rates"] and (any(not _same(price, rate, returned, "rate") for price in current["rates"])
                or not _same(current["value"], current["qty"] * rate, returned, "amount")) and not allow_draft:
            frappe.throw("已提交退料的批次或序列号估值不符合准确原耗料单价")
        value += current["qty"] * rate
        used[key] = total
    if not allow_draft and (returned.get("amount") in (None, "")
            or not _same(-_number(returned.get("amount")), value, returned, "amount")
            or not _same(-_number(returned.get("consumed_qty")) * _number(returned.get("rate")), value, returned, "amount")):
        frappe.throw("已提交退料金额与明确选择的原批次或序列号估值不一致")
    return value


class _FinishedLotRow(dict):
    """Expose a native FG movement to the shared lot proof in stock units."""

    def __init__(self, row, **values):
        super().__init__(values)
        self.row = row

    def __getattr__(self, field):
        return self.get(field)

    def precision(self, field):
        return _precision(self.row, "qty" if field == "consumed_qty" else field)


def has_finished_tracking(row):
    return any(row.get(field) for field in FG_TRACKING_FIELDS - {"use_serial_batch_fields"})


def _finished_movement(row, field, parent):
    rejected = field == "rejected_qty"
    return _FinishedLotRow(row, name=row.get("name"), parent=parent, doctype="Subcontracting Receipt Item",
        rm_item_code=row.get("item_code"), stock_uom=row.get("stock_uom"),
        consumed_qty=_number(row.get(field)) * _factor(row), rate=row.get("rate"), amount=row.get("amount"),
        serial_and_batch_bundle=row.get("rejected_serial_and_batch_bundle" if rejected else "serial_and_batch_bundle"),
        serial_no=row.get("rejected_serial_no" if rejected else "serial_no"), batch_no=row.get("batch_no"))


def finished_source_lots(source, original, warehouse, *, ledger=None, reader=None):
    fields = [field for field, column in (("qty", "warehouse"), ("rejected_qty", "rejected_warehouse"))
              if original.get(column) == warehouse and _number(original.get(field)) > 0]
    if len(fields) != 1:
        frappe.throw("成品批次退回仓库不能唯一绑定原合格或拒收入库来源")
    movement = _finished_movement(original, fields[0], source.name)
    ledger = ledger if ledger is not None else _source_stock_entries(source)
    rows = [entry for entry in ledger if entry.get("voucher_detail_no") == original.name
            and entry.get("warehouse") == warehouse and entry.get("item_code") == original.item_code]
    qty = sum((_number(entry.get("actual_qty")) for entry in rows), Decimal(0))
    value = sum((_number(entry.get("stock_value_difference")) for entry in rows), Decimal(0))
    if (qty <= 0 or value < 0 or not _same(qty, movement.consumed_qty, original)
            or any(_number(entry.get("actual_qty")) < 0 for entry in rows)):
        frappe.throw("原成品批次的有效库存数量或金额不完整")
    _bind_finished_stock_lots(movement, rows, original)
    movement.update(amount=value, rate=value / qty)
    if not _has_tracking(movement):
        frappe.throw("原成品实物缺少对应合格或拒收逐批次、逐序列号来源")
    pools = material_lots(movement, source.company, warehouse, original=True, reader=reader,
                           source_direction="Inward", return_direction="Outward")
    serials = {value.strip() for value in (movement.get("serial_no") or "").splitlines() if value.strip()}
    if (serials and serials != {key[1] for key in pools if key[0] == "serial"}
            or movement.get("batch_no") and any(pool["batch"] != movement.batch_no for pool in pools.values())):
        frappe.throw("原成品旧批次或序列号字段与实际历史凭证不一致")
    return movement, pools


def _bind_finished_stock_lots(movement, entries, row):
    """Bind legacy fields to their actual effective stock movement too.

    Native may convert a legacy selection to an SLE-only bundle. That bound
    bundle carries historical per-lot value; the row's aggregate rate cannot
    replace it. Truly legacy stock must prove its exact serial/batch identity.
    """
    entries = [entry for entry in entries if _number(entry.get("actual_qty"))]
    bundles = {entry.get("serial_and_batch_bundle") or "" for entry in entries}
    expected = movement.get("serial_and_batch_bundle")
    if expected:
        if bundles != {expected}:
            frappe.throw("原成品批次凭证与准确原行有效库存账没有绑定")
        return
    if bundles - {""}:
        if len(bundles) != 1:
            frappe.throw("原成品有效库存账没有唯一历史批次凭证，不能按平均价猜测")
        movement.update(serial_and_batch_bundle=next(iter(bundles)))
        return
    serials = {value.strip() for value in (movement.get("serial_no") or "").splitlines() if value.strip()}
    actual_serials = set()
    batch = movement.get("batch_no") or ""
    for entry in entries:
        listed = [value.strip() for value in (entry.get("serial_no") or "").splitlines() if value.strip()]
        if (len(listed) != len(set(listed)) or actual_serials.intersection(listed)
                or (entry.get("batch_no") or "") != batch):
            frappe.throw("原成品旧批次或序列号字段与有效库存账不一致")
        if listed and not _same(Decimal(len(listed)), abs(_number(entry.get("actual_qty"))), row):
            frappe.throw("原成品旧库存账序列号数量与实物数量不一致")
        actual_serials.update(listed)
    if serials != actual_serials or not serials and not batch:
        frappe.throw("原成品旧批次或序列号字段缺少准确有效库存来源")


def finished_return_value(source, original, returned, *, ledger=None, reader=None, used=None,
                          allow_draft=False, bundle=None, return_ledger=None, require_selection=None):
    """Actual explicitly selected FG value; fees keep their covered-qty basis.

    None denotes an unselected draft, never permission to post it. Lot quotas
    are separate for the exact source FG row and its accepted/rejected stock.
    """
    if not has_finished_tracking(original) and not has_finished_tracking(returned):
        return None
    if require_selection is None:
        require_selection = not allow_draft
    used = used if used is not None else {}
    value, selected_qty, missing = Decimal(0), Decimal(0), False
    for field, column in (("qty", "warehouse"), ("rejected_qty", "rejected_warehouse")):
        if not _number(returned.get(field)):
            continue
        warehouse = returned.get(column)
        origin, _pools = finished_source_lots(source, original, warehouse, ledger=ledger, reader=reader)
        current = _finished_movement(returned, field, returned.get("parent"))
        entries = None
        if return_ledger is not None:
            entries = [entry for entry in return_ledger if entry.get("voucher_detail_no") == returned.name
                       and entry.get("warehouse") == warehouse and entry.get("item_code") == returned.item_code]
            _bind_finished_stock_lots(current, entries, returned)
        if not _has_tracking(current):
            if allow_draft and not require_selection:
                missing = True
                continue
            frappe.throw("请用原生成品批次或序列号选择器明确选择本次退回实物后提交")
        chosen_bundle = bundle if bundle and bundle.get("name") == current.get("serial_and_batch_bundle") else None
        # The whole row may have two native warehouse movements. Check entry
        # prices here; validate its combined row amount/rate below.
        current.update(amount=None)
        chosen = material_lots(current, source.company, warehouse, allow_draft=allow_draft,
                               reader=reader, bundle=chosen_bundle, source_direction="Inward", return_direction="Outward")
        # Use the shared original-membership, mixed-price-layer and lot-cap
        # proof. Its row-total check is separate because this is an FG row.
        current_value = selected_material_value(origin, current, source.company, warehouse,
            reader=reader, bundle=chosen_bundle, used=used.setdefault((original.name, warehouse), {}),
            allow_draft=True, source_direction="Inward", return_direction="Outward")
        if not allow_draft and any(pool["rates"] and (
                not _same(pool["value"], pool["qty"] * (_pools[key]["value"] / _pools[key]["qty"]), returned, "amount")
                or any(not _same(rate, _pools[key]["value"] / _pools[key]["qty"], returned, "rate") for rate in pool["rates"]))
                for key, pool in chosen.items()):
            frappe.throw("已提交成品退回批次或序列号金额与原历史价值不一致")
        if return_ledger is not None:
            if (any(_number(entry.get("actual_qty")) > 0 for entry in entries)
                    or not _same(sum((_number(entry.get("actual_qty")) for entry in entries), Decimal(0)), current.consumed_qty, returned)
                    or not _same(-sum((_number(entry.get("stock_value_difference")) for entry in entries), Decimal(0)), current_value, returned, "amount")
                    or current.get("serial_and_batch_bundle") and any(entry.get("serial_and_batch_bundle") != current.serial_and_batch_bundle
                        for entry in entries if _number(entry.get("actual_qty")))):
                frappe.throw("成品退回选中批次与实际库存数量、金额或凭证绑定不一致")
        value += current_value
        selected_qty += -current.consumed_qty
    if missing:
        return None
    if not selected_qty:
        frappe.throw("成品批次退回必须有负数实物数量")
    if not allow_draft and (not _same(-_number(returned.get("amount")), value, returned, "amount")
            or not _same(-_number(returned.get("qty")) * _factor(returned) * _number(returned.get("rate"))
                         - _number(returned.get("rejected_qty")) * _factor(returned) * _number(returned.get("rate")), value, returned, "amount")):
        frappe.throw("成品退回行金额或单价与明确选择的原批次价值不一致")
    return value


def _finished_return_values(document, source, *, bundle=None, allow_draft=None):
    if not any(has_finished_tracking(item) for item in [*source.items, *document.items]):
        return {}
    _prior_raw_returns(source, document.get("name"))
    used = {}
    originals = {row.name: row for row in source.items}
    for parent in source._jifeng_return_prior_documents:
        for row in parent.items:
            original = originals.get(row.get("subcontracting_receipt_item"))
            if original is None:
                frappe.throw("既有成品退回没有准确原入库行")
            finished_return_value(source, original, row, used=used, return_ledger=_source_stock_entries(parent))
    if allow_draft is None:
        allow_draft = document.docstatus == 0 or document.get("_action") == "submit"
    return {row.name: finished_return_value(source, originals[row.subcontracting_receipt_item], row,
        used=used, allow_draft=allow_draft, bundle=bundle,
        require_selection=document.docstatus == 1 or document.get("_action") == "submit") for row in document.items}


def _validate_material_lot_returns(document, source):
    if not any(_has_tracking(raw) for raw in source.get("supplied_items") or []):
        return {}
    used, values = {}, {}
    for raw in _prior_raw_returns(source, document.get("name")):
        origin = _origin_raw(source, raw.source_fg_row, raw)
        if _has_tracking(origin):
            selected_material_value(origin, raw, source.company, source.supplier_warehouse,
                                    used=used.setdefault(origin.name, {}))
    rows = {row.name: row for row in document.items}
    draft = document.docstatus == 0 and document.get("_action") != "submit"
    for raw in document.get("supplied_items") or []:
        row = rows.get(raw.get("reference_name"))
        if not row:
            frappe.throw("批次退料没有准确的退回商品明细")
        origin = _origin_raw(source, row.subcontracting_receipt_item, raw)
        if _has_tracking(origin):
            if not _has_tracking(raw):
                if draft:
                    continue
                frappe.throw("请在原生批次或序列号选择器明确选择本次退回实物后提交")
            values[id(raw)] = selected_material_value(origin, raw, source.company, source.supplier_warehouse,
                # During SCR submit validation its selected bundle is still a
                # native draft; native SLE processing submits it afterwards.
                allow_draft=document.docstatus == 0 or document.get("_action") == "submit",
                used=used.setdefault(origin.name, {}))
    return values


def _prior_raw_returns(source, current_name):
    parents = frappe.db.sql("""
        select parent.name
        from `tabSubcontracting Receipt` parent
        where parent.docstatus = 1 and parent.is_return = 1
          and parent.return_against = %s and parent.name != %s
        order by parent.name
        for update
    """, (source.name, current_name or ""), as_dict=True)
    rows = frappe.db.sql("""
        select raw.*, fg.subcontracting_receipt_item as source_fg_row
        from `tabSubcontracting Receipt` parent
        inner join `tabSubcontracting Receipt Supplied Item` raw on raw.parent = parent.name
        inner join `tabSubcontracting Receipt Item` fg
          on fg.name = raw.reference_name and fg.parent = parent.name
        where parent.docstatus = 1 and parent.is_return = 1
          and parent.return_against = %s and parent.name != %s
          and raw.parenttype = 'Subcontracting Receipt'
          and raw.parentfield = 'supplied_items'
          and fg.parenttype = 'Subcontracting Receipt'
          and fg.parentfield = 'items'
        order by parent.name, raw.idx
        for update
    """, (source.name, current_name or ""), as_dict=True)
    if {row.parent for row in rows} - {parent.name for parent in parents}:
        frappe.throw("既有退料凭证范围不一致，不能核对剩余额度")
    documents = []
    for name in [parent.name for parent in parents]:
        parent = frappe.get_doc(RECEIPT, name, for_update=True)
        try:
            parent.check_permission("read")
        except frappe.PermissionError:
            frappe.throw("无法完整读取既有已提交退料的数量、批次和金额来源", frappe.PermissionError)
        _read_source_fields(parent)
        if (parent.docstatus != 1 or not cint(parent.get("is_return"))
                or parent.get("return_against") != source.name or parent.company != source.company
                or parent.supplier != source.supplier or parent.supplier_warehouse != source.supplier_warehouse):
            frappe.throw("既有退料的原收回、公司、供应商或仓库来源不一致")
        raw = {item.name: item for item in parent.get("supplied_items") or []}
        items = {item.name: item for item in parent.items}
        membership = frappe.get_all("Subcontracting Receipt Supplied Item", filters={"parent": name},
                                    pluck="name", limit_page_length=0)
        selected = [row for row in rows if row.parent == name]
        item_membership = frappe.get_all("Subcontracting Receipt Item", filters={"parent": name},
                                         pluck="name", limit_page_length=0)
        if (set(raw) != set(membership) or set(raw) != {row.name for row in selected}
                or set(items) != set(item_membership)
                or any(item.get("parenttype") != RECEIPT or item.get("parentfield") != "items"
                       for item in parent.items)):
            frappe.throw("既有退料明细来源不完整，不能按部分批次核对剩余额度")
        for item in parent.items:
            original = next((row for row in source.items if row.name == item.get("subcontracting_receipt_item")), None)
            if original is None:
                frappe.throw("既有退料没有准确的原收回商品明细")
            # A submitted return with missing raw rows must not make its lots
            # available again. Check exact source row membership, even if the
            # locking raw query yielded no rows for this parent.
            expected = {row.name for row in source.get("supplied_items") or []
                        if row.get("reference_name") == original.name and _number(row.get("consumed_qty"))}
            actual = [_origin_raw(source, original.name, row).name for row in raw.values()
                      if row.get("reference_name") == item.name and _number(row.get("consumed_qty"))]
            if set(actual) != expected or len(actual) != len(expected):
                frappe.throw("既有退料缺少或重复准确原耗料明细，不能核对剩余批次")
            ratio = sum(_physical_pools(item, original)) / _covered_stock(original)
            for material in [row for row in raw.values() if row.get("reference_name") == item.name]:
                origin = _origin_raw(source, original.name, material)
                if (material.get("parenttype") != RECEIPT or material.get("parentfield") != "supplied_items"
                        or not _same(-_number(material.get("consumed_qty")), _number(origin.get("consumed_qty")) * ratio, origin)):
                    frappe.throw("既有退料数量与准确原实物明细不一致，不能核对剩余批次")
        for row in selected:
            item = raw[row.name]
            fg = items.get(item.get("reference_name"))
            # Native Document.as_dict includes doctype and temporary fields
            # absent from SQL raw.*. Compare the actual database projection,
            # and check the native child's doctype independently.
            if (not fg or item.get("doctype") != "Subcontracting Receipt Supplied Item"
                    or fg.get("subcontracting_receipt_item") != row.source_fg_row
                    or any(item.get(field) != row.get(field) for field in row if field != "source_fg_row")):
                frappe.throw("既有退料与准确原商品或耗料明细不一致")
        documents.append(parent)
    source._jifeng_return_prior_documents = documents
    return rows


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
    used_lots = {}
    for raw in prior:
        original = _origin_raw(source, raw.source_fg_row, raw)
        if _has_tracking(original):
            selected_material_value(original, raw, document.company, source.supplier_warehouse,
                                    used=used_lots.setdefault(original.name, {}))
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
                          conversion_factor=float(_factor(row)), serial_and_batch_bundle=None,
                          serial_no="", batch_no="", use_serial_batch_fields=0)
            selected = next((old for old in current if old.get("reference_name") == row.name
                             and old.get("rm_item_code") == raw.rm_item_code
                             and old.get("stock_uom") == raw.stock_uom
                             and old.get("bom_detail_no") == raw.get("bom_detail_no")), None)
            if selected:
                current.remove(selected)
                copied["name"] = selected.name
            if _has_tracking(raw):
                _origin_raw(source, original.name, raw)
                # Prove original per-lot value against the effective supplier
                # SLE. A cancelled/unfinished repost or hidden ledger blocks.
                entries = [entry for entry in _source_stock_entries(source)
                           if entry.voucher_detail_no == raw.name]
                if (not _same(-sum((_number(entry.actual_qty) for entry in entries), Decimal(0)), _number(raw.consumed_qty), raw)
                        or not _same(-sum((_number(entry.stock_value_difference) for entry in entries), Decimal(0)), _number(raw.amount), raw, "amount")):
                    frappe.throw("原批次耗料与有效库存数量或估值不一致")
                if selected and _has_tracking(selected):
                    for field in ("serial_no", "batch_no", "serial_and_batch_bundle", "use_serial_batch_fields"):
                        copied[field] = selected.get(field)
                    candidate = dict(copied, parent=document.name)
                    amount = selected_material_value(raw, candidate, document.company, source.supplier_warehouse,
                        allow_draft=True, used=used_lots.setdefault(raw.name, {}))
                    copied["amount"], copied["rate"] = -float(amount), float(amount / qty)
                elif document.docstatus == 1 or document.get("_action") == "submit":
                    frappe.throw("请在原生批次或序列号选择器明确选择本次退回实物后提交")
                # Preparing/saving a draft only leaves an unselected material
                # row. Do not turn original history into a physical selection.
            elif selected and _has_tracking(selected):
                frappe.throw("原耗料没有该批次或序列号来源，不能退回其他批次实物")
            values.append(copied)
    if any(_has_tracking(raw) for raw in current):
        frappe.throw("已选择的退料批次没有唯一的原耗料明细，请用同一原耗料行的批次选择器处理")
    document.set("supplied_items", [])
    for value in values:
        document.append("supplied_items", value)


def _guard_return_allocation(document, source, proof, entries):
    """Do not accumulate penny rounding across separately posted reversals."""
    from copy import deepcopy
    from erpnext.accounts.general_ledger import (get_cost_center_allocation_data, get_field_precision,
                                                get_merge_properties, process_gl_map)
    if not any(len(get_cost_center_allocation_data(document.company, date, value.get("cost_center"))) > 1
               for values, date in ((proof.unallocated, source.posting_date), (entries, document.posting_date))
               for value in values if _number(value.get("debit")) or _number(value.get("credit"))):
        return
    fields = [field for field in get_merge_properties(receipt_accounting_dimensions())
              if field not in ("voucher_detail_no", "voucher_no")]
    precision = get_field_precision(frappe.get_meta("GL Entry").get_field("debit"),
                                    currency=frappe.get_cached_value("Company", document.company, "default_currency"))
    def vector(values):
        result = {}
        for value in process_gl_map(deepcopy(values), from_repost=True):
            key = tuple(value.get(field) or "" for field in fields)
            result[key] = result.get(key, Decimal(0)) + _number(value.get("debit")) - _number(value.get("credit"))
        return {key: _number(flt(value, precision)) for key, value in result.items() if flt(value, precision)}

    full = set()
    for original in source.items:
        current = [row for row in document.items if row.get("subcontracting_receipt_item") == original.name]
        if len(current) != 1 or _physical_pools(current[0], original) != (
                _number(original.get("qty")) * _factor(original),
                _number(original.get("rejected_qty")) * _factor(original)):
            continue
        before = vector([value for value in proof.unallocated if value.get("voucher_detail_no") == original.name])
        after = vector([value for value in entries if value.get("voucher_detail_no") == current[0].name])
        if after == {key: -value for key, value in before.items()}:
            full.add(original.name)
    source_charges = {charge.name for charge in source.get("additional_costs") or []}
    current_charges = {charge.name for charge in document.get("additional_costs") or []}
    charges_full = (full == {row.name for row in source.items}
        and vector([value for value in entries if value.get("voucher_detail_no") in current_charges]) ==
            {key: -value for key, value in vector([value for value in proof.unallocated
                                                   if value.get("voucher_detail_no") in source_charges]).items()})
    current_origins = {row.name: row.get("subcontracting_receipt_item") for row in document.items}
    active_origins = {row.get("subcontracting_receipt_item") for row in document.items
                      if _number(row.get("qty")) or _number(row.get("rejected_qty"))}
    active_charge_keys = {tuple(value.get(field) or "" for field in fields) for value in entries
                          if value.get("voucher_detail_no") in current_charges
                          and (_number(value.get("debit")) or _number(value.get("credit")))}
    for values, posting_date, charges, origins in ((proof.unallocated, source.posting_date, source_charges, {}),
                                                  (entries, document.posting_date, current_charges, current_origins)):
        for value in values:
            detail = value.get("voucher_detail_no")
            if (values is proof.unallocated and ((detail not in charges and detail not in active_origins)
                    or (detail in charges and tuple(value.get(field) or "" for field in fields) not in active_charge_keys))):
                continue
            if (detail in charges and charges_full) or origins.get(detail, detail) in full:
                continue
            allocation = get_cost_center_allocation_data(document.company, posting_date, value.get("cost_center"))
            if len(allocation) <= 1:
                continue
            for field in ("debit", "credit", "debit_in_account_currency", "credit_in_account_currency"):
                for _, percentage in allocation:
                    projected = _number(value.get(field)) * _number(percentage) / 100
                    if projected != _number(flt(projected, precision)):
                        frappe.throw("成本中心分摊的分批退回存在分钱舍入，无法准确累计反冲原已入账归属，请按准确整行退回核对")


class SubcontractingReceiptReturnMixin:
    def make_item_gl_entries(self, gl_entries, inventory_account_map=None, *, _source_proof=None):
        """Post exact SLE values and native accepted-quantity fees, even at zero FG value."""
        if (self.docstatus == 2 or any(row.get("secondary_item_type") or row.get("valuation_type")
                or _number(row.get("landed_cost_voucher_amount")) for row in self.items)):
            return super().make_item_gl_entries(gl_entries, inventory_account_map)
        from erpnext.subcontracting.doctype.subcontracting_receipt.subcontracting_receipt import get_account_currency
        first_entry = len(gl_entries)

        # Native submit/repost writer scope: only this submitted voucher. This
        # is not a user-facing reader and returns no unrelated ledger details.
        ledger = frappe.get_all("Stock Ledger Entry", filters={"voucher_type": RECEIPT,
            "voucher_no": self.name, "is_cancelled": 0}, fields=["name", "company", "item_code", "warehouse",
            "voucher_detail_no", "actual_qty", "stock_value_difference"], limit_page_length=0)
        if any(sle.company != self.company for sle in ledger):
            frappe.throw("收回会计库存来源公司不一致")
        source = None
        if _source_proof is not None:
            if (not isinstance(_source_proof, _ReceiptFeeProof) or not _source_proof.reversible
                    or not cint(self.get("is_return"))
                    or _source_proof.document.name != self.get("return_against")):
                frappe.throw("原收回费用须由本次服务器来源证明，不能用客户端标志跳过")
            source = _validate_source(_source_proof.document, self)
        elif cint(self.get("is_return")):
            source = _source(self.get("return_against"), self)
        proof = None
        if source:
            # This also covers zero-fee and rejected-only returns. An original
            # native zero-qty gate with no GL must never gain an inverse GL.
            proof = _source_proof or _prove_receipt_fees(source, for_return=True)
            if any((self.get(field) or "") != (source.get(field) or "") for field in
                    ("cost_center", "project", "finance_book", *receipt_accounting_dimensions())):
                frappe.throw("退回的会计维度或费用分配来源与原收回不一致，不能改写原费用归属")
        originals = {row.name: row for row in source.items} if source else {}
        known, allocated_other = set(), Decimal(0)
        remarks = self.get("remarks") or frappe._("Accounting Entry for Stock")
        inventory_account_map = inventory_account_map if inventory_account_map is not None else frappe._dict()

        def account(row, warehouse_field=None):
            result = self.get_inventory_account_dict(row, inventory_account_map, warehouse_field)
            if not result.get("account"):
                frappe.throw("收回实物仓库缺少库存科目，不能只记部分会计分录")
            return result

        def entry(account_name, center, *, debit=0, credit=0, against=None, item=None, detail=None):
            self.add_gl_entry(gl_entries=gl_entries, account=account_name, cost_center=center,
                debit=float(debit), credit=float(credit), remarks=remarks, against_account=against,
                account_currency=get_account_currency(account_name), project=item.get("project") if item else None,
                item=item, voucher_detail_no=item.name if item is not None else detail)

        def movement(row, item_code, warehouse, expected):
            selected = [sle for sle in ledger if sle.voucher_detail_no == row.name
                        and sle.item_code == item_code and sle.warehouse == warehouse]
            if not selected or not _same(sum((_number(sle.actual_qty) for sle in selected), Decimal(0)), expected, row):
                frappe.throw("收回会计缺少准确行、仓库或实物数量的有效库存账，不能把缺失估值当零")
            known.update(sle.name for sle in selected)
            return sum((_number(sle.stock_value_difference) for sle in selected), Decimal(0))

        for row in self.items:
            original = originals.get(row.get("subcontracting_receipt_item")) if source else None
            if source and original is None:
                frappe.throw("退回会计没有准确原收回商品明细")
            if original and (any((row.get(field) or "") != (original.get(field) or "") for field in
                    ("expense_account", "cost_center", "project", *receipt_accounting_dimensions()))
                    or (row.get("service_expense_account") or row.get("expense_account")) !=
                       (original.get("service_expense_account") or original.get("expense_account"))):
                frappe.throw("退回商品的会计科目或维度与准确原明细不一致")
            center = row.get("cost_center")
            fg_value, material_value = Decimal(0), Decimal(0)
            for qty_field, warehouse_field in (("qty", "warehouse"), ("rejected_qty", "rejected_warehouse")):
                qty = _number(row.get(qty_field)) * _factor(row)
                if not qty:
                    continue
                value = movement(row, row.item_code, row.get(warehouse_field), qty)
                fg_value += value
                warehouse_row = frappe._dict(row.as_dict())
                warehouse_row.warehouse = row.get(warehouse_field)
                inv = account(warehouse_row)
                entry(inv.account, center, debit=value, against=row.expense_account, item=row)
                entry(row.expense_account, center, credit=value, against=inv.account, item=row)

            for raw in self.get("supplied_items") or []:
                if raw.get("reference_name") != row.name or not _number(raw.get("consumed_qty")):
                    continue
                value = movement(raw, raw.rm_item_code, self.supplier_warehouse, -_number(raw.consumed_qty))
                if not _same(-value, _number(raw.get("amount")), raw, "amount"):
                    frappe.throw("收回会计原料金额与准确有效库存账不一致")
                material_value -= value
                inv = account(raw, "supplier_warehouse")
                expense = raw.get("expense_account") or row.expense_account
                raw_center = raw.get("cost_center") or center
                if original:
                    origin = _origin_raw(source, original.name, raw)
                    if (expense != (origin.get("expense_account") or original.expense_account)
                            or raw_center != (origin.get("cost_center") or original.cost_center)):
                        frappe.throw("退回耗料的会计科目或成本中心与准确原耗料不一致")
                entry(inv.account, raw_center, debit=value, against=expense, item=row)
                entry(expense, raw_center, credit=value, against=inv.account, item=row)

            processing = receipt_accounting_fee(self, row, "service_cost_per_qty", original, fee_source=source, _proof=proof)
            other = receipt_accounting_fee(self, row, "additional_cost_per_qty", original, fee_source=source, _proof=proof)
            allocated_other += other
            service_account = row.get("service_expense_account") or row.expense_account
            entry(service_account, center, credit=processing, against=row.expense_account, item=row)
            entry(row.expense_account, center, debit=processing, against=service_account, item=row)
            entry(row.expense_account, self.get("cost_center") or self.get_company_default("cost_center"), debit=other, detail=row.name)
            difference = material_value + processing + other - fg_value
            if _number(flt(difference, _precision(row, "amount"))):
                loss_account = self.get_company_default("stock_adjustment_account", ignore_validation=True)
                if not loss_account:
                    frappe.throw("收回估值差额缺少库存调整科目")
                entry(loss_account, center, debit=difference, against=row.expense_account, item=row)
                entry(row.expense_account, center, credit=difference, against=loss_account, item=row)

        if known != {sle.name for sle in ledger}:
            frappe.throw("收回会计存在未能绑定准确实物或耗料的库存账，不能只记部分来源")
        charged_other = Decimal(0)
        for charge in self.get("additional_costs") or []:
            value = _number(charge.get("base_amount") if charge.get("base_amount")
                or charge.get("account_currency") != self.company_currency else charge.get("amount"))
            charged_other += value
            entry(charge.expense_account, self.get("cost_center") or self.get_company_default("cost_center"), credit=value, detail=charge.name)
        if flt(allocated_other, self.precision("total_additional_costs")) != flt(charged_other, self.precision("total_additional_costs")):
            frappe.throw("收回已保存附加费用分配与原费用单本币金额不一致，不能凑平会计分录")
        if source:
            _guard_return_allocation(self, source, proof, gl_entries[first_entry:])
        if source and str(self.posting_date) != str(source.posting_date):
            # Native allocation is date-sensitive. A new allocation must not
            # redirect a historical reversal into different cost centers.
            from copy import deepcopy
            from erpnext.accounts.general_ledger import get_merge_properties, process_gl_map
            current = deepcopy(gl_entries[first_entry:])
            historical = deepcopy(current)
            for value in historical:
                value["posting_date"] = source.posting_date
            keys = get_merge_properties(receipt_accounting_dimensions())
            precision = self.precision("total_additional_costs")
            def allocation_totals(values):
                result = {}
                for value in process_gl_map(values, from_repost=True):
                    key = tuple(value.get(field) or "" for field in keys)
                    result[key] = result.get(key, Decimal(0)) + _number(value.get("debit")) - _number(value.get("credit"))
                return {key: flt(value, precision) for key, value in result.items() if flt(value, precision)}
            if allocation_totals(current) != allocation_totals(historical):
                frappe.throw("退回日期的成本中心分摊与原已入账归属不同，不能改写原费用分配")

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
            total += receipt_accounting_fee(self, row, "additional_cost_per_qty", original)
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
        selected_finished_values = _finished_return_values(self, source)
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
            selected_value = selected_finished_values.get(row.name)
            row.amount = -float(selected_value if selected_value is not None else accepted * accepted_rate + rejected * rejected_rate)
            if selected_value is None and accepted and rejected and not _same(accepted_rate, rejected_rate, row, "rate"):
                frappe.throw("接收和拒收原入库单价不同，请分开两行退回实物")
            row.rate = float(selected_value / (accepted + rejected) if selected_value is not None else accepted_rate if accepted else rejected_rate)
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


class SubcontractingReceiptMaterialReturnBundleMixin:
    def get_valuation_rate_for_return_entry(self, return_against):
        """Supply the SCR raw-row mapping missing in native v16.36.

        Native set_valuation_rate_for_return_entry still performs its serial,
        batch, FIFO queue and stock checks and writes rates. It receives the
        operator's selected original lots, never a server-picked allocation.
        """
        self._jifeng_finished_return_rates = None
        if (self.get("voucher_type") == RECEIPT and self.get("type_of_transaction") == "Outward"
                and self.get("voucher_no") and self.get("voucher_detail_no")
                and frappe.db.exists("Subcontracting Receipt Item", self.voucher_detail_no)):
            receipt = frappe.get_doc(RECEIPT, self.voucher_no)
            receipt.check_permission("read")
            if receipt.docstatus == 2 or self.get("is_cancelled") or not cint(receipt.get("is_return")):
                return super().get_valuation_rate_for_return_entry(return_against)
            _read_source_fields(receipt)
            if receipt.get("return_against") != return_against:
                frappe.throw("成品批次退回凭证与原收回来源不一致")
            row = next((item for item in receipt.items if item.name == self.voucher_detail_no), None)
            if not row or self.name not in (row.get("serial_and_batch_bundle"), row.get("rejected_serial_and_batch_bundle")):
                frappe.throw("成品退回批次没有绑定操作者选择的准确退回行")
            source = _source(return_against, receipt)
            original = next((item for item in source.items if item.name == row.get("subcontracting_receipt_item")), None)
            if original is None:
                frappe.throw("成品退回批次没有准确原入库行")
            validate_physical_return(receipt, source=source)
            _finished_return_values(receipt, source, bundle=self, allow_draft=True)
            _movement, pools = finished_source_lots(source, original, self.warehouse)
            _check_serial_return_valuation(original.item_code, pools)
            rates = _lot_rate_mapping(pools)
            self._jifeng_finished_return_rates = rates
            return rates
        if (self.get("voucher_type") != RECEIPT or self.get("type_of_transaction") != "Inward"
                or not self.get("voucher_no") or not self.get("voucher_detail_no")
                or not frappe.db.exists("Subcontracting Receipt Supplied Item", self.voucher_detail_no)):
            return super().get_valuation_rate_for_return_entry(return_against)
        receipt = frappe.get_doc(RECEIPT, self.voucher_no)
        receipt.check_permission("read")
        if receipt.docstatus == 2 or not cint(receipt.get("is_return")):
            return super().get_valuation_rate_for_return_entry(return_against)
        _read_source_fields(receipt)
        if receipt.get("return_against") != return_against:
            frappe.throw("退料批次凭证与原收回来源不一致")
        raw = next((row for row in receipt.get("supplied_items") or [] if row.name == self.voucher_detail_no), None)
        fg = next((row for row in receipt.items if raw and row.name == raw.reference_name), None)
        if not fg or _number(raw.get("consumed_qty")) >= 0:
            frappe.throw("退料批次凭证没有准确的负数原耗料明细")
        source = _source(return_against, receipt)
        original = _origin_raw(source, fg.subcontracting_receipt_item, raw)
        if not _has_tracking(original):
            frappe.throw("原耗料缺少逐批次或逐序列号历史来源，不能推测退料单价")
        # Validate the original bundle against effective material SLE too.
        ledger = [entry for entry in _source_stock_entries(source) if entry.voucher_detail_no == original.name]
        if (not _same(-sum((_number(entry.actual_qty) for entry in ledger), Decimal(0)), _number(original.consumed_qty), original)
                or not _same(-sum((_number(entry.stock_value_difference) for entry in ledger), Decimal(0)), _number(original.amount), original, "amount")):
            frappe.throw("原批次耗料与有效库存数量或估值不一致")
        used = {}
        for prior in _prior_raw_returns(source, receipt.name):
            origin = _origin_raw(source, prior.source_fg_row, prior)
            if origin.name == original.name:
                selected_material_value(original, prior, source.company, source.supplier_warehouse, used=used)
        selected_material_value(original, raw, source.company, source.supplier_warehouse,
                                allow_draft=self.docstatus == 0, used=used, bundle=self)
        pools = material_lots(original, source.company, source.supplier_warehouse, original=True)
        _check_serial_return_valuation(original.rm_item_code, pools)
        return _lot_rate_mapping(pools)

    def get_batchwise_return_avg_rates(self):
        rates = super().get_batchwise_return_avg_rates()
        historical = getattr(self, "_jifeng_finished_return_rates", None)
        if historical is not None:
            for batch, rate in rates.items():
                original = historical["batches"].get(batch)
                row = next((item for item in self.entries if item.get("batch_no") == batch), self)
                qty = sum((abs(_number(item.get("qty"))) for item in self.entries if item.get("batch_no") == batch), Decimal(0))
                if (original is None or not _same(_number(rate), _number(original), row, "incoming_rate")
                        or not _same(qty * _number(rate), qty * _number(original), row, "stock_value_difference")):
                    frappe.throw("该成品批次当前原生估值与原收回历史金额不同，无法按准确原价退回，须明确原估值层处理")
        return rates


def _lot_rate_mapping(pools):
    mapping = {"serial_nos": {key[1]: float(pool["value"] / pool["qty"])
                           for key, pool in pools.items() if key[0] == "serial"},
            "batches": {key[1]: float(pool["value"] / pool["qty"])
                        for key, pool in pools.items() if key[0] == "batch"}}
    for key, pool in pools.items():
        if key[0] == "serial" and pool["batch"]:
            # Native checks both identities on a serial+batch entry. Its
            # serial branch still applies the exact per-serial price.
            mapping["batches"].setdefault(pool["batch"], 0.0)
    return mapping


def _check_serial_return_valuation(item_code, pools):
    if any(key[0] == "serial" for key in pools):
        item = frappe.get_doc("Item", item_code)
        item.check_permission("read")
        _read_fields("Item", {"has_serial_no", "use_serial_no_wise_valuation"})
        if not cint(item.get("has_serial_no")) or not cint(item.get("use_serial_no_wise_valuation")):
            frappe.throw("该序列号商品未启用原生逐号估值，不能保证准确原单价退回过账")


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
        # Mapping a return is a draft preparation, not a physical choice.
        # Native legacy mapping can copy/choose source serials automatically.
        for field in FG_TRACKING_FIELDS:
            row.set(field, 0 if field == "use_serial_batch_fields" else None)
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
