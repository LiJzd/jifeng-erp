"""Preserve native return validation while separating accepted and rejected pools."""

from contextvars import ContextVar
from decimal import Decimal, InvalidOperation
from functools import wraps

import frappe
from frappe.utils import cint, flt


_validation_context = ContextVar("jifeng_purchase_return_validation", default=None)
_VALIDATOR_MARKER = "_jifeng_purchase_return_dispatcher"


def _number(value):
    try:
        result = Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw("采购退货数量或单位换算不是有效数字")
    if not result.is_finite():
        frappe.throw("采购退货数量或单位换算不是有限数字")
    return result


def _precision(source):
    precision = source.precision("stock_qty")
    return int(precision if precision is not None else 6)


def _over_return(message):
    from erpnext.controllers.sales_and_purchase_return import StockOverReturnError

    frappe.throw(message, StockOverReturnError)


def _source(doc, key, args, cache):
    if not (isinstance(key, tuple) and len(key) == 2 and key[1]
            and args.get("purchase_receipt_item") == key[1] and args.item_code == key[0]):
        frappe.throw("采购退货必须关联精确的原收货明细，不能按同名商品猜测额度")
    cache_key = ("source", doc.return_against)
    if cache_key not in cache:
        # This source lock stays held until Frappe commits the caller's document.
        # Two operators must not both consume the same pre-submit remainder.
        source_doc = frappe.get_doc("Purchase Receipt", doc.return_against, for_update=True)
        source_doc.check_permission("read")
        if (source_doc.docstatus != 1 or source_doc.is_return or source_doc.company != doc.company
                or source_doc.supplier != doc.supplier):
            frappe.throw("采购退货的原收货必须为相同公司和供应商的已提交原单")
        cache[cache_key] = source_doc
    source_doc = cache[cache_key]
    source = next((row for row in source_doc.items if row.name == key[1]), None)
    if not source or source.item_code != args.item_code or source.stock_uom != args.stock_uom:
        frappe.throw("采购退货商品、库存单位或明细来源与原收货不一致")
    return source_doc, source


def _submitted_returns(doc, source_doc, cache):
    cache_key = ("returns", source_doc.name, doc.name)
    if cache_key in cache:
        return cache[cache_key]
    # The bound must include every submitted return, including returns that the
    # current operator cannot list. FOR UPDATE is also a current read, so an old
    # REPEATABLE READ snapshot cannot hide the submission that held our source
    # lock immediately before this transaction acquired it.
    rows = frappe.db.sql("""
        select child.name, child.parent, child.item_code, child.stock_uom,
               child.purchase_receipt_item, child.qty, child.stock_qty,
               child.rejected_qty, child.conversion_factor, child.warehouse,
               child.rejected_warehouse, child.return_qty_from_rejected_warehouse,
               parent.company as return_company, parent.supplier as return_supplier
        from `tabPurchase Receipt` parent
        inner join `tabPurchase Receipt Item` child on child.parent = parent.name
        where parent.docstatus = 1 and parent.is_return = 1 and parent.return_against = %s
          and parent.name != %s and child.parenttype = 'Purchase Receipt'
        order by parent.name, child.idx
        for update
    """, (source_doc.name, doc.name or ""), as_dict=True)
    if any(row.return_company != source_doc.company or row.return_supplier != source_doc.supplier for row in rows):
        frappe.throw("既有采购退货与原收货的公司或供应商不一致，请先核对")
    cache[cache_key] = rows
    return rows


def _movement(row, source, submitted):
    factor = _number(row.get("conversion_factor"))
    qty, rejected = _number(row.get("qty")), _number(row.get("rejected_qty"))
    if factor <= 0 or qty > 0 or rejected > 0:
        frappe.throw("采购实物退货数量必须为负数，库存单位换算必须大于零")
    accepted = -qty * factor
    if submitted:
        stored = _number(row.get("stock_qty"))
        if stored > 0 or flt(-stored, _precision(source)) != flt(accepted, _precision(source)):
            frappe.throw("既有退货库存数量与凭证数量换算不一致，请先核对")
        accepted = -stored
    rejected = -rejected * factor
    if cint(row.get("return_qty_from_rejected_warehouse")):
        if rejected or (accepted and (not source.rejected_warehouse or row.warehouse != source.rejected_warehouse)):
            frappe.throw("退拒收数量或仓库与原收货拒收明细不一致")
        return Decimal(0), accepted
    if accepted and row.warehouse != source.warehouse:
        frappe.throw("采购退接收仓库与原收货明细不一致")
    if rejected and (not source.rejected_warehouse or row.rejected_warehouse != source.rejected_warehouse):
        frappe.throw("采购退拒收仓库与原收货明细不一致")
    return accepted, rejected


def _quantity_inputs(doc, key, args, ref, already_returned_items, cache):
    source_doc, source = _source(doc, key, args, cache)
    prior_accepted = prior_rejected = Decimal(0)
    all_source_rows = {row.name: row for row in source_doc.items}
    for row in _submitted_returns(doc, source_doc, cache):
        if row.item_code != source.item_code:
            continue
        source_name = row.purchase_receipt_item
        if not source_name or source_name not in all_source_rows:
            frappe.throw("既有退货缺少有效的原收货明细，不能猜测已退额度")
        if source_name != source.name:
            continue
        if row.stock_uom != source.stock_uom:
            frappe.throw("既有采购退货的库存单位与原收货不一致")
        accepted, rejected = _movement(row, source, submitted=True)
        prior_accepted += accepted
        prior_rejected += rejected
    # Native validation checks each child independently. Other lines in THIS
    # document must consume the same source's remaining pools as well.
    for row in doc.items:
        if row is args or row.item_code != source.item_code:
            continue
        if not row.get("purchase_receipt_item"):
            frappe.throw("采购退货必须关联精确的原收货明细")
        if row.purchase_receipt_item != source.name:
            continue
        if row.stock_uom != source.stock_uom:
            frappe.throw("同单采购退货库存单位与原收货不一致")
        accepted, rejected = _movement(row, source, submitted=False)
        prior_accepted += accepted
        prior_rejected += rejected
    current_accepted, current_rejected = _movement(args, source, submitted=False)
    source_factor = _number(source.conversion_factor)
    accepted_limit = _number(source.stock_qty)
    rejected_limit = _number(source.rejected_qty) * source_factor
    if source_factor <= 0 or accepted_limit < 0 or rejected_limit < 0:
        frappe.throw("原采购收货的库存数量或换算系数异常，请先核对")
    precision = _precision(source)
    if flt(prior_accepted + current_accepted, precision) > flt(accepted_limit, precision):
        _over_return("采购退接收数量超过原收货明细的剩余可退库存数量")
    if flt(prior_rejected + current_rejected, precision) > flt(rejected_limit, precision):
        _over_return("采购退拒收数量超过原收货明细的剩余可退库存数量")
    corrected = dict(already_returned_items)
    totals = dict(already_returned_items.get(key) or {})
    if cint(args.get("return_qty_from_rejected_warehouse")):
        totals.update(stock_qty=prior_rejected, received_qty=prior_rejected, rejected_qty=prior_rejected)
    else:
        totals.update(stock_qty=prior_accepted, received_qty=prior_accepted + prior_rejected,
                      rejected_qty=prior_rejected)
    corrected[key] = totals
    # Return mappers can carry an old stock_qty; native BuyingController converts
    # it AFTER AccountsController invokes this validator. Normalize copies for
    # this read-only validation, retaining the document and source snapshots.
    normalized_args = frappe._dict(args.as_dict() if callable(getattr(args, "as_dict", None)) else args)
    normalized_args.stock_qty = float(_number(args.qty) * _number(args.conversion_factor))
    normalized_args.received_qty = float(_number(args.qty) + _number(args.rejected_qty))
    normalized_ref = frappe._dict(ref)
    # Native "already returned" rejects a fully used pool even when this line
    # requests zero from that pool. Exact requested pools were bounded above;
    # a zero reference suppresses only that irrelevant zero-quantity check.
    if not current_accepted and not cint(args.get("return_qty_from_rejected_warehouse")):
        normalized_ref.stock_qty = 0
    if not current_rejected:
        normalized_ref.rejected_qty = 0
    return corrected, normalized_args, normalized_ref


def install_quantity_dispatcher():
    """Install one permanent dispatch; never patch/unpatch around a request."""
    from erpnext.controllers import sales_and_purchase_return

    original = sales_and_purchase_return.validate_quantity
    if getattr(original, _VALIDATOR_MARKER, False):
        return original

    @wraps(original)
    def validate_quantity(doc, key, args, ref, valid_items, already_returned_items):
        if not (isinstance(doc, PurchaseReceiptMixin) and doc.doctype == "Purchase Receipt" and doc.is_return):
            return original(doc, key, args, ref, valid_items, already_returned_items)
        context = _validation_context.get()
        cache = context[1] if context and context[0] is doc else {}
        corrected, normalized_args, normalized_ref = _quantity_inputs(
            doc, key, args, ref, already_returned_items, cache)
        return original(doc, key, normalized_args, normalized_ref, valid_items, corrected)

    setattr(validate_quantity, _VALIDATOR_MARKER, True)
    sales_and_purchase_return.validate_quantity = validate_quantity
    return validate_quantity


class PurchaseReceiptMixin:
    def validate(self):
        install_quantity_dispatcher()
        token = _validation_context.set((self, {}))
        try:
            return super().validate()
        finally:
            _validation_context.reset(token)
