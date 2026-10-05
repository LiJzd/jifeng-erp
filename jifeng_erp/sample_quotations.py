"""Approved sample identities through native quotations, orders and flows.

Sample production qty and commercial quotation qty are separate explicit inputs.
ERPNext retains pricing, taxes, currencies, submit/cancel and stock conversion.
Saved SO drafts reserve quotation stock quantity as well as submitted orders.
"""

from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
import hashlib
import json
from uuid import UUID, uuid4

import frappe
from frappe.utils import getdate, today

from jifeng_erp.flow_quantity import check_quantity_fields, sales_row_snapshot
from jifeng_erp.personnel_separation import _atomic


SOURCE_FIELDS = (
    "jf_sample_order",
    "jf_sample_order_item",
    "jf_sample_source_digest",
    "jf_sample_source_snapshot",
)
REQUEST_FIELDS = ("jf_sample_request_id", "jf_sample_request_digest")
FLOW_SOURCE_FIELDS = SOURCE_FIELDS + ("jf_source_quotation", "jf_source_quotation_item")
_QUOTATION_CREATING = ContextVar("jifeng_sample_quotation_creation", default=None)
_AMEND_VALIDATED = object()
NUMERIC_HISTORY = {
    "qty",
    "stock_qty",
    "conversion_factor",
    "rate",
    "price_list_rate",
    "amount",
    "base_rate",
    "base_amount",
    "net_rate",
    "net_amount",
    "base_net_rate",
    "base_net_amount",
    "discount_percentage",
    "discount_amount",
    "conversion_rate",
    "total",
    "net_total",
    "base_total",
    "grand_total",
    "base_grand_total",
}
COMMERCIAL_ROWS = (
    "item_code",
    "qty",
    "uom",
    "stock_uom",
    "stock_qty",
    "conversion_factor",
    "rate",
    "price_list_rate",
    "amount",
    "base_rate",
    "base_amount",
    "net_rate",
    "net_amount",
    "base_net_rate",
    "base_net_amount",
    "discount_percentage",
    "discount_amount",
    "jf_requirements",
    "jf_packaging",
    "jf_requirements_attachment",
    "jf_make_flow",
)
QUOTE_FIELDS = (
    "name",
    "company",
    "quotation_to",
    "party_name",
    "currency",
    "valid_till",
    "items",
    "jf_sample_order",
) + REQUEST_FIELDS
QUOTE_ITEM_FIELDS = (
    "name",
    "item_code",
    "qty",
    "uom",
    "stock_uom",
    "stock_qty",
    "conversion_factor",
    "rate",
) + SOURCE_FIELDS
SO_ITEM_FIELDS = (
    "name",
    "item_code",
    "qty",
    "uom",
    "stock_uom",
    "stock_qty",
    "conversion_factor",
    "prevdoc_docname",
    "quotation_item",
) + SOURCE_FIELDS


class SampleQuotationError(ValueError):
    pass


class SampleQuotationWriteUncertain(RuntimeError):
    pass


def _quotation_atomic(method):
    action = _atomic(method)

    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return action(*args, **kwargs)
        except Exception as error:
            if any(
                "局部回滚清理失败" in note for note in getattr(error, "__notes__", [])
            ):
                raise SampleQuotationWriteUncertain(
                    "生成结果尚未确认，请仅核对原创建回执"
                ) from error
            raise

    return wrapped


def decimal_value(value, label, *, zero=False):
    if isinstance(value, bool) or value in (None, ""):
        raise SampleQuotationError(f"请明确填写{label}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise SampleQuotationError(f"{label}必须是有效数字") from exc
    if not result.is_finite() or result < 0 or (not zero and result == 0):
        raise SampleQuotationError(
            f"{label}必须是{'非负' if zero else '大于零的'}有限数字"
        )
    if result > Decimal("999999999999.999999999"):
        raise SampleQuotationError(f"{label}超过原生字段可保存范围")
    return result


def _text(value, label):
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 140
    ):
        frappe.throw(f"请明确选择{label}")
    return value


def _uuid(value):
    try:
        parsed = UUID(value)
        valid = parsed.version == 4 and str(parsed) == value
    except (ValueError, AttributeError, TypeError):
        valid = False
    if not valid:
        frappe.throw("创建请求回执无效，请重新打开生成窗口")
    return value


def _json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def source_snapshot(sample, row):
    return {
        "sample_order": sample.name,
        "sample_item": row.name,
        "approved_digest": sample.approved_digest,
        "company": sample.company,
        "item_code": row.item_code,
        "stock_uom": row.stock_uom,
        "production_qty": str(decimal_value(row.qty, "样品制作数量")),
        **{
            key: row.get(key) or ""
            for key in (
                "sample_identity",
                "product_profile",
                "bom",
                "requirements",
                "packaging",
                "requirements_attachment",
            )
        },
    }


def _sample(name, *, version=None, lock=False, active=True):
    from jifeng_erp import sample_orders

    if active:
        return sample_orders.read_sample_source(name, version=version, for_update=lock)
    # Cancelled historical quotations remain readable after reverse cancellation.
    return sample_orders.read_sample_history(name, for_update=lock)


def _sample_row(sample, name):
    matches = [row for row in sample.get("items") or [] if row.name == name]
    if len(matches) != 1:
        frappe.throw("准确样品明细已变化，请重新选择")
    return matches[0]


def _check_sources(quote, *, active=True, lock=False):
    quote.check_permission("read")
    check_quantity_fields("Quotation", QUOTE_FIELDS)
    check_quantity_fields("Quotation Item", QUOTE_ITEM_FIELDS, parenttype="Quotation")
    source_name = quote.get("jf_sample_order")
    if not source_name:
        if any(
            any(row.get(key) for key in SOURCE_FIELDS)
            for row in quote.get("items") or []
        ):
            frappe.throw("报价样品来源必须同时有准确父单和明细")
        return None
    sample = _sample(source_name, active=active, lock=lock)
    if quote.company != sample.company or quote.quotation_to != "Customer":
        frappe.throw("样品报价必须属于同一公司并明确选择客户")
    if sample.get("customer") and quote.party_name != sample.customer:
        frappe.throw("报价客户与样品客户不一致，请核对")
    if not quote.get("items"):
        frappe.throw("样品报价必须选择准确样品明细")
    for row in quote.get("items"):
        if row.get("jf_sample_order") != sample.name:
            frappe.throw("报价明细不能混用其他样品来源")
        origin = _sample_row(sample, row.get("jf_sample_order_item"))
        snapshot = source_snapshot(sample, origin)
        if (
            row.get("jf_sample_source_digest") != sample.approved_digest
            or row.get("jf_sample_source_snapshot") != _json(snapshot)
            or row.item_code != origin.item_code
            or row.get("stock_uom") not in (None, "", origin.stock_uom)
        ):
            frappe.throw("报价样品来源或审核快照已变化，请核对")
    return sample


def _protected_history(doc, *, parent_fields=()):
    previous = doc.get_doc_before_save()
    if not previous:
        return
    if any(previous.get(key) != doc.get(key) for key in parent_fields):
        frappe.throw("已保存的样品来源和请求回执不能替换或清除")
    originals = {
        row.name: row
        for row in previous.get("items") or []
        if row.get("jf_sample_order")
    }
    for row in doc.get("items") or []:
        old = originals.pop(row.name, None)
        if old and any(old.get(key) != row.get(key) for key in SOURCE_FIELDS):
            frappe.throw("已保存的样品明细来源不能替换或清除")
    if originals:
        frappe.throw("已保存的样品来源明细不能删除；请处理原单并重新生成")


def _history_value(field, value):
    if field in NUMERIC_HISTORY and value not in (None, ""):
        return Decimal(str(value))
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if field in {"transaction_date", "valid_till", "delivery_date"} and value:
        return str(getdate(value))
    return value


def _cancel_history(doc, parent_fields):
    previous = doc.get_doc_before_save()
    if not previous:
        frappe.throw("反审缺少原保存记录，请重新打开原单")
    for field in parent_fields:
        if _history_value(field, previous.get(field)) != _history_value(
            field, doc.get(field)
        ):
            frappe.throw("反审时不能修改原单公司、客户、数量或金额")
    original_rows = {row.name: row for row in previous.get("items") or []}
    current_rows = {row.name: row for row in doc.get("items") or []}
    if set(original_rows) != set(current_rows):
        frappe.throw("反审时不能增删原单明细")
    for name, row in current_rows.items():
        if any(
            _history_value(field, row.get(field))
            != _history_value(field, original_rows[name].get(field))
            for field in COMMERCIAL_ROWS + SOURCE_FIELDS
        ):
            frappe.throw("反审时不能修改原单公司、客户、数量或金额")


def _quotation_scope(doc):
    return bool(
        doc
        and (
            doc.get("jf_sample_order")
            or any(doc.get(key) for key in REQUEST_FIELDS)
            or any(
                any(row.get(key) for key in SOURCE_FIELDS)
                for row in doc.get("items") or []
            )
        )
    )


def _receipt(doc):
    if not doc.get("jf_sample_order"):
        frappe.throw("创建请求回执只能属于从样品生成的报价")
    _uuid(doc.get("jf_sample_request_id"))
    digest = doc.get("jf_sample_request_digest")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        frappe.throw("样品报价缺少有效服务器创建回执，请从样品制作单生成")


def _amend_origin(doc):
    """Load the exact parent without broadening an ordinary native amendment."""
    name = doc.get("amended_from")
    if not name:
        return None
    origin = frappe.get_doc(doc.doctype, _text(name, "原修改单据"), for_update=True)
    return origin


def _check_amend_origin(origin):
    origin.check_permission("read")
    check_quantity_fields(origin.doctype, ("name", "amended_from", "docstatus", "items"))
    if origin.docstatus != 2:
        frappe.throw("请先反审原单，再保存修改版")
    for row in origin.get("items") or []:
        if (row.get("parent"), row.get("parenttype"), row.get("parentfield")) != (
            origin.name, origin.doctype, "items"
        ):
            frappe.throw("原修改单据缺少准确明细归属，请核对")


def _amend_row(row, original_rows, fields):
    # Desk's native Amend places _amended_from on each copied child. It is
    # untrusted input: only an exact row in the persisted cancelled parent can
    # supply an origin. Never infer a row from Item, position or a similar qty.
    identity = row.get("_amended_from")
    if identity:
        matches = [old for old in original_rows if old.name == identity]
    else:
        # Native API callers can preserve the full exact source identity.
        # Repeated commercial rows from the same sample need the Desk row ID.
        matches = [
            old for old in original_rows
            if row.get("jf_sample_order_item")
            and all(row.get(field) == old.get(field) for field in fields)
        ]
    if len(matches) != 1:
        frappe.throw("修改版缺少原单准确明细，请从原单“修改”并保留明细来源")
    old = matches[0]
    if row.item_code != old.item_code:
        frappe.throw("样品修改版不能替换原明细商品，请从样品制作单重新生成")
    return old


def _fill_amend_sources(row, old, fields):
    for field in fields:
        if row.get(field) not in (None, "", old.get(field)):
            frappe.throw("修改版来源与原单准确明细不一致")
        row.set(field, old.get(field))


def _prepare_sample_quotation_amend(doc):
    if not doc.get("amended_from") or doc.get_doc_before_save():
        return False
    origin = _amend_origin(doc)
    if not _quotation_scope(origin):
        return False
    _check_amend_origin(origin)
    _receipt(origin)
    _check_sources(origin, active=False, lock=True)
    sample = _sample(origin.jf_sample_order, lock=True)
    if doc.get("jf_sample_order") not in (None, "", origin.jf_sample_order):
        frappe.throw("修改版不能替换原样品来源")
    doc.jf_sample_order = origin.jf_sample_order
    if not doc.get("items"):
        frappe.throw("样品报价修改版必须保留准确样品明细")
    identities, requested = set(), []
    for row in doc.get("items"):
        old = _amend_row(row, origin.get("items"), SOURCE_FIELDS)
        if old.name in identities:
            frappe.throw("修改版不能重复引用同一原报价明细，请核对")
        identities.add(old.name)
        _fill_amend_sources(row, old, SOURCE_FIELDS)
        try:
            requested.append({
                "quotation_item": old.name,
                "qty": format(decimal_value(row.qty, "报价数量").normalize(), "f"),
                "uom": _text(row.get("uom"), "报价单位"),
                "rate": format(decimal_value(row.rate, "报价单价", zero=True).normalize(), "f"),
                "make_flow": row.get("jf_make_flow"),
            })
        except SampleQuotationError as error:
            frappe.throw(str(error))
    # before_validate and validate both run before the first insert. The private
    # object cannot be supplied in RPC JSON and scopes the receipt to this doc.
    prepared = getattr(doc, "_jf_sample_amend", None)
    if isinstance(prepared, tuple) and len(prepared) == 5 and prepared[0] is _AMEND_VALIDATED:
        if prepared[1:] != (
            origin.name, doc.get("jf_sample_request_id"),
            doc.get("jf_sample_request_digest"), requested,
        ):
            frappe.throw("原生报价改变了修改版指定数量、单位或单价，请核对价格设置")
    else:
        # Ignore copied client receipts: an amendment is a new creation.
        doc.jf_sample_request_id = str(uuid4())
        doc.jf_sample_request_digest = _digest({
            "kind": "quotation_amend", "amended_from": origin.name,
            "sample_order": sample.name, "approved_digest": sample.approved_digest,
            "company": doc.get("company"), "customer": doc.get("party_name"),
            "currency": doc.get("currency"), "valid_till": str(doc.get("valid_till")),
            "items": requested,
        })
        doc._jf_sample_amend = (
            _AMEND_VALIDATED, origin.name, doc.jf_sample_request_id,
            doc.jf_sample_request_digest, requested,
        )
    return True


def validate_sample_quotation(doc, method=None):
    amended = _prepare_sample_quotation_amend(doc)
    previous = doc.get_doc_before_save()
    if not _quotation_scope(doc) and not _quotation_scope(previous):
        return
    _protected_history(doc, parent_fields=("jf_sample_order", "amended_from") + REQUEST_FIELDS)
    if not previous:
        context = _QUOTATION_CREATING.get()
        if (
            not amended and (not context
            or context[0] is not doc
            or context[1:]
            != (doc.get("jf_sample_request_id"), doc.get("jf_sample_request_digest")))
        ):
            frappe.throw("样品报价和创建回执只能从样品制作单生成")
    _receipt(doc)
    if getattr(doc, "_action", None) == "cancel":
        return
    history = (
        getattr(doc, "_action", None) == "update_after_submit"
        or method == "before_update_after_submit"
    )
    _check_sources(doc, active=not history, lock=True)


def load_sample_quotation(doc, method=None):
    if _quotation_scope(doc):
        _receipt(doc)
        _check_sources(doc, active=False)


def cancel_sample_quotation(doc, method=None):
    if not _quotation_scope(doc) and not _quotation_scope(doc.get_doc_before_save()):
        return
    _protected_history(doc, parent_fields=("jf_sample_order", "amended_from") + REQUEST_FIELDS)
    _cancel_history(
        doc,
        (
            "company",
            "quotation_to",
            "party_name",
            "currency",
            "conversion_rate",
            "transaction_date",
            "valid_till",
            "total",
            "net_total",
            "grand_total",
            "base_total",
            "base_grand_total",
            "discount_amount",
        ),
    )
    _receipt(doc)
    _check_sources(doc, active=False, lock=True)
    # Integrity reads include hidden descendants but never return their identities.
    if frappe.db.sql(
        """SELECT so.name FROM `tabSales Order` so
        JOIN `tabSales Order Item` soi ON soi.parent=so.name
        WHERE so.docstatus<2 AND soi.prevdoc_docname=%s LIMIT 1""",
        (doc.name,),
    ):
        frappe.throw("请先处理引用本报价的销售订单，再反审报价")


def _result(doc, request_id):
    return {
        "status": "found",
        "doctype": "Quotation",
        "name": doc.name,
        "request_id": request_id,
    }


def _deleted_request(request_id):
    # Native Deleted Document retains deleted draft payloads. Avoid creating a
    # new quote with a known UUID after an explicit native draft deletion.
    rows = frappe.db.sql(
        """SELECT data FROM `tabDeleted Document`
        WHERE deleted_doctype='Quotation' AND data LIKE %s""",
        ("%" + request_id + "%",),
    )
    for row in rows:
        data = json.loads(row[0])
        if isinstance(data, dict) and data.get("jf_sample_request_id") == request_id:
            return True
    return False


@frappe.whitelist(methods=["GET"])
def resolve_sample_quotation(request_id):
    request_id = _uuid(request_id)
    if not frappe.has_permission("Quotation", "read"):
        frappe.throw("没有报价读取权限", frappe.PermissionError)
    name = frappe.db.get_value(
        "Quotation", {"jf_sample_request_id": request_id}, "name"
    )
    if not name:
        return {
            "status": "deleted" if _deleted_request(request_id) else "absent",
            "doctype": "Quotation",
            "request_id": request_id,
        }
    doc = frappe.get_doc("Quotation", name)
    doc.check_permission("read")
    _receipt(doc)
    _check_sources(doc, active=False)
    return _result(doc, request_id)


@frappe.whitelist(methods=["POST"])
@_quotation_atomic
def make_sample_quotation(
    sample_order, version, customer, currency, valid_till, items, request_id
):
    request_id = _uuid(request_id)
    customer, currency = _text(customer, "客户"), _text(currency, "报价币种")
    if not isinstance(valid_till, str) or len(valid_till) != 10:
        frappe.throw("请明确填写报价有效期")
    try:
        date = getdate(valid_till)
        if str(date) != valid_till or date < getdate(today()):
            raise ValueError
    except (ValueError, TypeError):
        frappe.throw("报价有效期必须是今天或以后的准确日期")
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except (ValueError, TypeError):
            frappe.throw("报价明细格式无效")
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        frappe.throw("请选择1至100行准确样品明细")
    rows = []
    try:
        for row in items:
            if not isinstance(row, dict) or set(row) != {
                "sample_item",
                "qty",
                "uom",
                "rate",
                "make_flow",
            }:
                raise SampleQuotationError("报价明细字段不完整")
            if row["make_flow"] not in (0, 1, False, True):
                raise SampleQuotationError("请明确选择是否生成生产流程")
            rows.append(
                {
                    "sample_item": _text(row["sample_item"], "样品明细"),
                    "qty": str(decimal_value(row["qty"], "报价数量")),
                    "rate": str(decimal_value(row["rate"], "报价单价", zero=True)),
                    "uom": _text(row["uom"], "报价单位"),
                    "make_flow": int(row["make_flow"]),
                }
            )
    except SampleQuotationError as error:
        frappe.throw(str(error))
    sample = _sample(_text(sample_order, "样品制作单"), version=version, lock=True)
    if not frappe.has_permission("Quotation", "create"):
        frappe.throw("没有报价单创建权限", frappe.PermissionError)
    frappe.get_doc("Customer", customer).check_permission("select")
    frappe.get_doc("Currency", currency).check_permission("select")
    if sample.get("customer") and sample.customer != customer:
        frappe.throw("报价客户与样品客户不一致，请核对")
    payload = {
        "sample_order": sample.name,
        "approved_digest": sample.approved_digest,
        "customer": customer,
        "currency": currency,
        "valid_till": valid_till,
        "items": rows,
    }
    digest = _digest(payload)
    existing = frappe.db.get_value(
        "Quotation", {"jf_sample_request_id": request_id}, "name"
    )
    if existing:
        doc = frappe.get_doc("Quotation", existing)
        doc.check_permission("read")
        _receipt(doc)
        _check_sources(doc, active=False)
        if doc.get("jf_sample_request_digest") != digest:
            frappe.throw("创建回执已有其他内容，不能重复使用")
        return _result(doc, request_id)
    if _deleted_request(request_id):
        frappe.throw("这个请求的原报价已删除，请核对删除记录并重新打开生成窗口")
    doc = frappe.new_doc("Quotation")
    doc.update(
        {
            "quotation_to": "Customer",
            "party_name": customer,
            "company": sample.company,
            "currency": currency,
            "valid_till": valid_till,
            "transaction_date": today(),
            "ignore_pricing_rule": 1,
            "jf_sample_order": sample.name,
            "jf_sample_request_id": request_id,
            "jf_sample_request_digest": digest,
        }
    )
    doc.set("items", [])
    for row in rows:
        origin = _sample_row(sample, row["sample_item"])
        doc.append(
            "items",
            {
                "item_code": origin.item_code,
                "qty": row["qty"],
                "uom": row["uom"],
                "rate": row["rate"],
                "price_list_rate": row["rate"],
                "stock_uom": origin.stock_uom,
                "jf_sample_order": sample.name,
                "jf_sample_order_item": origin.name,
                "jf_sample_source_digest": sample.approved_digest,
                "jf_sample_source_snapshot": _json(source_snapshot(sample, origin)),
                "jf_requirements": origin.get("requirements"),
                "jf_packaging": origin.get("packaging"),
                "jf_requirements_attachment": origin.get("requirements_attachment"),
                "jf_make_flow": row["make_flow"],
            },
        )
    token = _QUOTATION_CREATING.set((doc, request_id, digest))
    try:
        doc.insert()
    finally:
        _QUOTATION_CREATING.reset(token)
    _check_sources(doc)
    # Native item defaults and rounding must never silently replace explicit inputs.
    for saved, requested in zip(doc.get("items"), rows, strict=True):
        if (
            saved.uom != requested["uom"]
            or Decimal(str(saved.qty)) != Decimal(requested["qty"])
            or Decimal(str(saved.rate)) != Decimal(requested["rate"])
        ):
            frappe.throw("原生报价改变了指定数量、单位或单价，请核对精度及价格设置")
        try:
            sales_row_snapshot(saved, saved.stock_uom, saved.precision("stock_qty"))
        except ValueError as error:
            frappe.throw(str(error))
    return _result(doc, request_id)


def _candidate(row):
    if any(row.get(key) for key in SOURCE_FIELDS):
        return True
    if not row.get("prevdoc_docname") or not row.get("quotation_item"):
        return False
    if not frappe.get_meta("Quotation Item").has_field("jf_sample_order"):
        return False
    return bool(
        frappe.db.get_value("Quotation Item", row.quotation_item, "jf_sample_order")
    )


def _quote_row(order, row, *, lock=False, active=True):
    parent, identity = row.get("prevdoc_docname"), row.get("quotation_item")
    if not parent or not identity:
        frappe.throw("样品销售明细缺少准确报价父单与明细")
    if lock:
        frappe.db.get_value("Quotation", parent, "name", for_update=True)
    quote = frappe.get_doc("Quotation", parent)
    return _checked_quote_row(order, row, quote, lock=lock, active=active)


def _checked_quote_row(order, row, quote, *, lock=False, active=True):
    """Full quotation sources precede receipts and the selected-row checks."""
    identity = row.get("quotation_item")
    if not identity or not row.get("prevdoc_docname") or quote.name != row.prevdoc_docname:
        frappe.throw("样品销售明细缺少准确报价父单与明细")
    _check_sources(quote, lock=lock, active=active)
    _receipt(quote)
    if (
        (quote.docstatus != 1 if active else quote.docstatus not in (1, 2))
        or quote.company != order.company
        or quote.party_name != order.customer
    ):
        frappe.throw("销售订单与已审核样品报价的公司或客户不一致")
    matches = [item for item in quote.get("items") if item.name == identity]
    if (
        len(matches) != 1
        or matches[0].item_code != row.item_code
        or not matches[0].get("jf_sample_order")
    ):
        frappe.throw("销售订单的准确报价明细已变化")
    return quote, matches[0]


def _historical_sales_quote_row(order, row, quote):
    """Fixed business body for the private complete saved SO/Quote/Sample gates."""
    result = _checked_quote_row(order, row, quote, lock=False, active=False)
    if any(row.get(field) != result[1].get(field) for field in SOURCE_FIELDS):
        frappe.throw("销售订单样品来源与准确报价明细不一致")
    return result


def prepare_sample_sales_order(doc, method=None):
    if doc.get("amended_from") and not doc.get_doc_before_save():
        origin = _amend_origin(doc)
        old_candidates = [row for row in origin.get("items") or [] if _candidate(row)]
        if old_candidates:
            _check_amend_origin(origin)
            load_sample_sales_order(origin)
            check_quantity_fields(
                "Sales Order Item", SO_ITEM_FIELDS, parenttype="Sales Order"
            )
            identities = set()
            ordinary_ids = {row.name for row in origin.get("items") or [] if not _candidate(row)}
            for row in doc.get("items") or []:
                inherited = row.get("_amended_from") in {r.name for r in old_candidates}
                if not inherited and not _candidate(row):
                    if row.get("_amended_from") not in ordinary_ids:
                        frappe.throw("样品销售订单修改版缺少准确原明细，请从原单“修改”并保留来源")
                    # Existing mixed ordinary rows retain native behavior with
                    # their exact original ID. An untraced row cannot masquerade
                    # as a new ordinary item after clearing its sample identity.
                    continue
                old = _amend_row(
                    row, old_candidates,
                    SOURCE_FIELDS + ("prevdoc_docname", "quotation_item"),
                )
                if old.name in identities:
                    frappe.throw("修改版不能重复引用同一原销售明细，请核对")
                identities.add(old.name)
                _fill_amend_sources(
                    row, old, SOURCE_FIELDS + ("prevdoc_docname", "quotation_item")
                )
            if not identities:
                frappe.throw("样品销售订单修改版必须保留准确原样品明细，请从有效报价重新生成")
    previous = doc.get_doc_before_save()
    protected = any(_candidate(row) for record in (doc, previous) if record
                    for row in record.get("items") or [])
    _protected_history(doc, parent_fields=("amended_from",) if protected else ())
    candidates = [row for row in doc.get("items") or [] if _candidate(row)]
    if not candidates:
        return
    for name in sorted(
        {row.get("prevdoc_docname") for row in candidates if row.get("prevdoc_docname")}
    ):
        frappe.db.get_value("Quotation", name, "name", for_update=True)
    for row in candidates:
        _quote, origin = _quote_row(doc, row, lock=True)
        for field in SOURCE_FIELDS:
            if row.get(field) and row.get(field) != origin.get(field):
                frappe.throw("销售订单样品来源不能替换准确报价身份")
            row.set(field, origin.get(field))


def remaining_capacity(source_stock_qty, reserved_stock_qty, requested_stock_qty):
    total = decimal_value(source_stock_qty, "报价库存数量")
    reserved = decimal_value(reserved_stock_qty, "已转订单库存数量", zero=True)
    requested = decimal_value(requested_stock_qty, "本单库存数量")
    if reserved + requested > total:
        raise SampleQuotationError(
            "报价数量已被销售订单或未取消草稿占用，请核对分批转单数量"
        )
    return total - reserved - requested


def validate_sample_sales_order(doc, method=None):
    candidates = [row for row in doc.get("items") or [] if _candidate(row)]
    if not candidates:
        return
    check_quantity_fields("Sales Order", ("name", "company", "customer", "items"))
    check_quantity_fields("Sales Order Item", SO_ITEM_FIELDS, parenttype="Sales Order")
    grouped = {}
    for row in candidates:
        quote, origin = _quote_row(doc, row, lock=True)
        if any(row.get(field) != origin.get(field) for field in SOURCE_FIELDS):
            frappe.throw("销售订单缺少准确样品来源快照")
        try:
            sales_row_snapshot(row, origin.stock_uom, row.precision("stock_qty"))
            sales_row_snapshot(origin, origin.stock_uom, origin.precision("stock_qty"))
        except ValueError as error:
            frappe.throw(str(error))
        key = (quote.name, origin.name)
        bucket = grouped.setdefault(
            key, {"source": origin.stock_qty, "qty": Decimal(0)}
        )
        bucket["qty"] += decimal_value(row.stock_qty, "本单库存数量")
    for (parent, child), bucket in grouped.items():
        # Never truncate a hidden order from the integrity pool. No identities
        # or private values from other orders are returned to the caller.
        reserved = frappe.db.sql(
            """SELECT COALESCE(SUM(soi.stock_qty),0)
            FROM `tabSales Order Item` soi JOIN `tabSales Order` so ON so.name=soi.parent
            WHERE soi.prevdoc_docname=%s AND soi.quotation_item=%s
              AND so.docstatus<2 AND soi.docstatus<2 AND so.name<>%s""",
            (parent, child, doc.name or ""),
        )[0][0]
        try:
            remaining_capacity(bucket["source"], reserved, bucket["qty"])
        except SampleQuotationError as error:
            frappe.throw(str(error))


def load_sample_sales_order(doc, method=None):
    for row in doc.get("items") or []:
        if _candidate(row):
            check_quantity_fields(
                "Sales Order", ("name", "company", "customer", "items")
            )
            check_quantity_fields(
                "Sales Order Item", SO_ITEM_FIELDS, parenttype="Sales Order"
            )
            _quote, origin = _quote_row(doc, row, active=False)
            if any(row.get(field) != origin.get(field) for field in SOURCE_FIELDS):
                frappe.throw("销售订单样品历史来源快照不一致，请核对")


def guard_sample_sales_order_history(doc, method=None):
    previous = doc.get_doc_before_save()
    protected = any(_candidate(row) for record in (doc, previous) if record
                    for row in record.get("items") or [])
    _protected_history(doc, parent_fields=("amended_from",) if protected else ())
    if not previous:
        return
    old = {
        row.name: row
        for row in previous.get("items") or []
        if row.get("jf_sample_order")
    }
    for row in doc.get("items") or []:
        origin = old.get(row.name)
        if origin and any(
            row.get(key) != origin.get(key)
            for key in ("prevdoc_docname", "quotation_item")
        ):
            frappe.throw("审核后或反审时不能更改样品报价来源")
    if old and getattr(doc, "_action", None) == "cancel":
        _cancel_history(
            doc,
            (
                "company",
                "customer",
                "currency",
                "conversion_rate",
                "transaction_date",
                "delivery_date",
                "total",
                "net_total",
                "grand_total",
                "base_total",
                "base_grand_total",
                "discount_amount",
            ),
        )


def sample_fields_for_flow(order, row, *, active=True, lock=False):
    if not _candidate(row):
        return {}
    quote, origin = _quote_row(order, row, active=active, lock=lock)
    if any(row.get(field) != origin.get(field) for field in SOURCE_FIELDS):
        frappe.throw("销售订单样品来源快照不完整，不能生成流程单")
    return {
        **{field: origin.get(field) for field in SOURCE_FIELDS},
        "jf_source_quotation": quote.name,
        "jf_source_quotation_item": origin.name,
        # _quote_row has checked this exact JSON against the approved sample.
        # This is a creation default, not an immutable production/source field.
        "profile": json.loads(origin.jf_sample_source_snapshot)["product_profile"] or None,
    }


def _sample_flow_candidate(flow):
    if any(flow.get(field) for field in FLOW_SOURCE_FIELDS):
        return True
    if not flow.get("sales_order") or not flow.get("sales_order_item"):
        return False
    if not frappe.get_meta("Sales Order Item").has_field("jf_sample_order"):
        return False
    # Internal existence only: do not omit a sample origin because its Flow
    # projection was cleared, or reveal any hidden source values to the caller.
    return bool(
        frappe.db.get_value(
            "Sales Order Item", flow.sales_order_item, "jf_sample_order"
        )
    )


def check_sample_flow_source(flow, *, active=True, lock=False):
    """Exact SO child -> quotation child -> approved sample, without partial reads."""
    if not _sample_flow_candidate(flow):
        return
    if (
        not flow.get("sales_order")
        or not flow.get("sales_order_item")
        or flow.get("external_order")
    ):
        frappe.throw("样品流程单必须准确关联正式销售订单明细")
    flow.check_permission("read")
    check_quantity_fields(
        "Jifeng Flow Order",
        (
            "name",
            "company",
            "item_code",
            "sales_order",
            "sales_order_item",
            "source_key",
        )
        + FLOW_SOURCE_FIELDS,
    )
    order = frappe.get_doc("Sales Order", flow.sales_order, for_update=lock)
    order.check_permission("read")
    check_quantity_fields(
        "Sales Order", ("name", "company", "customer", "docstatus", "status", "items")
    )
    check_quantity_fields(
        "Sales Order Item", SO_ITEM_FIELDS + ("jf_make_flow",), parenttype="Sales Order"
    )
    matches = [
        row for row in order.get("items") or [] if row.name == flow.sales_order_item
    ]
    if len(matches) != 1:
        frappe.throw("样品流程单缺少准确销售订单明细")
    row = matches[0]
    if (
        row.get("parent") != order.name
        or row.get("parenttype") != "Sales Order"
        or row.get("parentfield") != "items"
        or flow.company != order.company
        or flow.item_code != row.item_code
    ):
        frappe.throw("样品流程单的销售订单身份、公司或商品不一致")
    if active and (
        order.docstatus != 1 or order.status == "Closed" or not row.get("jf_make_flow")
    ):
        frappe.throw("样品流程单必须来自已提交、未关闭且勾选生产的销售订单明细")
    if not active and order.docstatus not in (1, 2):
        frappe.throw("样品流程单历史销售来源未曾提交，请核对")
    from jifeng_erp.jushuitan import record_key

    if flow.get("source_key") != record_key(f"sales-order:{order.name}", row.name):
        frappe.throw("样品流程单来源唯一标识不一致")
    expected = sample_fields_for_flow(order, row, active=active, lock=lock)
    if not expected or any(
        flow.get(field) != expected.get(field) for field in FLOW_SOURCE_FIELDS
    ):
        frappe.throw("样品流程单的准确来源或批准快照不能替换、清除或伪造")


def validate_sample_flow(doc, method=None):
    previous = doc.get_doc_before_save()
    if not _sample_flow_candidate(doc) and not (
        previous and _sample_flow_candidate(previous)
    ):
        return
    if previous and any(
        previous.get(field) != doc.get(field)
        for field in FLOW_SOURCE_FIELDS
        + (
            "sales_order",
            "sales_order_item",
            "company",
            "item_code",
            "source_key",
            "external_order",
        )
    ):
        frappe.throw("已保存样品流程单的来源身份和批准快照不能修改或清除")
    # Existing source cancellation/closed status and memo updates retain the
    # immutable historical origin; only creation/production requires active sources.
    check_sample_flow_source(doc, active=not bool(previous), lock=True)
