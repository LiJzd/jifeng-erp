"""Explicit stock-unit quantities for flows; historical quantities are not inferred.

Native Sales Order.stock_qty is authoritative. The installed v16 selling
controller derives it from qty * conversion_factor, possibly rounded to its
native field precision. Work Order.qty is compared with Sales Order.stock_qty.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP, localcontext
import re


STOCK_BASIS = "stock_v1"
MANUAL_BASIS = "manual_verified_v1"
PENDING_BASIS = "pending"
SOURCE_FIELDS = ("source_qty", "source_uom", "source_stock_uom", "source_conversion_factor", "source_stock_qty",
                 "source_stock_precision")
QUANTITY_FIELDS = ("qty", "uom", "stock_uom", "conversion_factor", "stock_qty")
PLAN_FIELDS = ("planned_qty", "stock_uom", "quantity_basis")
CONFIRMATION_FIELDS = ("stock_plan_confirmation_note", "stock_plan_confirmed_at", "stock_plan_confirmed_by")


class FlowQuantityError(ValueError):
    pass


def positive_quantity(value, label):
    if isinstance(value, bool) or value in (None, ""):
        raise FlowQuantityError(f"{label}必须是大于零的有限数量")
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise FlowQuantityError(f"{label}必须是大于零的有限数量") from exc
    if not quantity.is_finite() or quantity <= 0:
        raise FlowQuantityError(f"{label}必须是大于零的有限数量")
    return quantity


def native_precision(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 9:
        raise FlowQuantityError("数量精度缺失或无效，请核对原生字段精度")
    return value


def representable_quantity(value, label, precision=9):
    quantity = positive_quantity(value, label)
    precision = native_precision(precision)
    # The installed MariaDB backend stores every Float as DECIMAL(21,9).
    # Reject before save instead of letting the database overflow or round it.
    if quantity > Decimal("999999999999.999999999"):
        raise FlowQuantityError(f"{label}超过原生数量字段的可保存范围")
    with localcontext() as context:
        context.prec = 50
        rounded = quantity.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)
    if rounded != quantity:
        raise FlowQuantityError(f"{label}最多允许{precision}位小数，请明确核对，系统不会自动舍入")
    return quantity


def plan_quantity(value, precision=9):
    return representable_quantity(value, "计划库存数量", precision)


def unit_name(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise FlowQuantityError("来源单位或库存单位缺失，请先核对单位，不能按名称猜测换算")
    return value


def sales_row_snapshot(row, item_stock_uom, stock_precision=6):
    """Read an already authorized native SO row; never substitute missing units.

    The caller passes the native stock_qty field precision when available. A
    rounded native quantity is accepted only at that precision. The returned
    stock_qty retains the exact persisted native value rather than a new rounding.
    """
    unit, stock_unit = unit_name(row.get("uom")), unit_name(row.get("stock_uom"))
    if stock_unit != unit_name(item_stock_uom):
        raise FlowQuantityError("销售来源的库存单位与商品库存单位不一致，请核对原生来源")
    qty = representable_quantity(row.get("qty"), "源订单数量")
    factor = representable_quantity(row.get("conversion_factor"), "源订单换算系数")
    stock_qty = representable_quantity(row.get("stock_qty"), "源订单库存数量")
    if unit == stock_unit and factor != 1:
        raise FlowQuantityError("销售单位与库存单位相同时，来源换算系数必须为 1")
    stock_precision = native_precision(stock_precision)
    quantum = Decimal(1).scaleb(-stock_precision)
    try:
        with localcontext() as context:
            context.prec = 50
            exact = qty * factor
            # Native flt permits commercial or banker's rounding. Both are
            # installed supported methods, and the historical setting need not
            # be the current one. Accept only their possible persisted results,
            # or the unrounded native result at the database's nine decimals.
            expected = {exact.quantize(quantum, rounding=mode) for mode in (ROUND_HALF_UP, ROUND_HALF_EVEN)}
            expected.add(exact.quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise FlowQuantityError("来源数量或换算系数超过可核对范围") from exc
    if stock_qty not in expected:
        raise FlowQuantityError("源订单库存数量与订单数量、换算系数不一致，请核对原生来源")
    return {"quantity_basis": STOCK_BASIS, "stock_uom": stock_unit,
            "source_qty": qty, "source_uom": unit, "source_stock_uom": stock_unit,
            "source_conversion_factor": factor, "source_stock_qty": stock_qty, "source_stock_precision": stock_precision}


def source_snapshot_changed(flow, snapshot):
    """Missing legacy snapshots are changes requiring explicit acknowledgement."""
    for field in SOURCE_FIELDS:
        before, after = flow.get(field), snapshot.get(field)
        if field in ("source_uom", "source_stock_uom"):
            if not before or before != after:
                return True
        elif field == "source_stock_precision":
            try:
                if native_precision(before) != native_precision(after):
                    return True
            except FlowQuantityError:
                return True
        else:
            try:
                if positive_quantity(before, "历史来源数量") != positive_quantity(after, "来源数量"):
                    return True
            except FlowQuantityError:
                return True
    return False


def require_stock_plan(flow, item_stock_uom):
    """Execution gate, including explicitly verified external plans.

    An external manual verification promises the plan's stock unit only. It
    does not claim that an unknown upstream quantity/unit was converted.
    """
    basis = flow.get("quantity_basis")
    if basis not in (STOCK_BASIS, MANUAL_BASIS):
        raise FlowQuantityError("流程单计划数量尚未核对库存单位，请先确认计划库存单位")
    if basis == MANUAL_BASIS and not flow.get("external_order"):
        raise FlowQuantityError("人工计划单位核对只适用于外部来源订单")
    if unit_name(flow.get("stock_uom")) != unit_name(item_stock_uom):
        raise FlowQuantityError("流程单计划库存单位与当前商品库存单位不一致，请先核对")
    if flow.get("sales_order"):
        # Missing source units cannot be cleared by the ordinary acknowledge
        # button. Recheck the stored snapshot without querying an Item fallback.
        sales_row_snapshot({"qty": flow.get("source_qty"), "uom": flow.get("source_uom"),
                            "stock_uom": flow.get("source_stock_uom"),
                            "conversion_factor": flow.get("source_conversion_factor"),
                            "stock_qty": flow.get("source_stock_qty")}, item_stock_uom,
                           stock_precision=flow.get("source_stock_precision"))
    if basis == MANUAL_BASIS and (not flow.get("stock_plan_confirmation_note")
                                 or not flow.get("stock_plan_confirmed_at")
                                 or not flow.get("stock_plan_confirmed_by")):
        raise FlowQuantityError("外部来源计划库存单位缺少明确核对记录")
    precision_method = getattr(flow, "precision", None)
    precision = precision_method("planned_qty") if callable(precision_method) else 9
    return plan_quantity(flow.get("planned_qty"), precision)


def same_version(expected, actual):
    """Only a full native modified timestamp is an optimistic version token."""
    if (not isinstance(expected, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?", expected)):
        return False
    try:
        return datetime.fromisoformat(expected) == datetime.fromisoformat(str(actual))
    except (ValueError, TypeError):
        return False


def check_quantity_fields(doctype, fields, permission_type="read", parenttype=None):
    """Native read/write and mask fields, independent of full document reads."""
    import frappe
    from frappe.model import get_permitted_fields

    columns = set(get_permitted_fields(doctype, parenttype=parenttype,
                                      permission_type=permission_type, ignore_virtual=True))
    meta = frappe.get_meta(doctype)
    access = set(meta.get_permlevel_access(permission_type=permission_type, parenttype=parenttype,
                                          user=frappe.session.user))
    levels = set(meta.get_permlevel_access(permission_type="mask", parenttype=parenttype, user=frappe.session.user))
    tables = set()
    for name in fields:
        field = meta.get_field(name)
        if not field or field.fieldtype not in ("Table", "Table MultiSelect"):
            continue
        # Table is a NO_VALUE_FIELD and is absent from get_permitted_fields.
        # Authorize the table itself through native Meta permlevel and mask.
        if (frappe.session.user != "Administrator" and
                ((field.permlevel or 0) not in access or
                 (field.get("mask") and (field.permlevel or 0) not in levels))):
            frappe.throw("没有完整核对计划数量或来源单位字段的权限", frappe.PermissionError)
        tables.add(name)
    if frappe.session.user != "Administrator":
        columns -= {field.fieldname for field in meta.fields
                    if field.get("mask") and (field.permlevel or 0) not in levels}
    if not (set(fields) - tables).issubset(columns):
        frappe.throw("没有完整核对计划数量或来源单位字段的权限", frappe.PermissionError)


def read_item_stock_uom(item_code, *, for_update=False):
    import frappe

    item = frappe.get_doc("Item", item_code, for_update=for_update)
    item.check_permission("read")
    check_quantity_fields("Item", ("stock_uom",))
    return unit_name(item.get("stock_uom"))


def read_sales_quantity_snapshot(order, row):
    """Exact native SO child + table/field permissions, not a guessed Item unit."""
    import frappe

    order.check_permission("read")
    check_quantity_fields("Sales Order", ("name", "company", "docstatus", "status", "items"))
    check_quantity_fields("Sales Order Item", ("item_code",) + QUANTITY_FIELDS, parenttype="Sales Order")
    table = frappe.get_meta("Sales Order").get_field("items")
    if not table or table.fieldtype != "Table" or table.options != "Sales Order Item":
        frappe.throw("销售订单数量来源表定义不一致，请核对原生来源")
    originals = [item for item in order.get("items") or [] if item.name == row.get("name")]
    if (row.get("parent") != order.name or row.get("parenttype") != "Sales Order"
            or row.get("parentfield") != "items"
            or not row.get("name") or len(originals) != 1
            or any(originals[0].get(field) != row.get(field) for field in ("item_code",) + QUANTITY_FIELDS)):
        frappe.throw("销售订单数量缺少准确来源明细，请核对原生来源")
    precision = row.precision("stock_qty")
    return sales_row_snapshot(row, read_item_stock_uom(row.item_code), precision)


def assert_no_quantity_downstream(flow_name):
    """Current internal existence reads: hidden linked sources also block edits.

    Incoming writes must take the same Flow lock before persisting a link.
    Native existing-document saves can lock their parent/children first; these
    reverse probes use NOWAIT so confirmation never waits on that parent.
    No source identifier is returned to the user.
    """
    import frappe

    parents = (("Work Order", "jf_flow_order"), ("Job Card", "jf_flow_order"),
               ("Stock Entry", "jf_material_issue_flow"), ("Jifeng Cost Preview", "flow_order"))
    children = (("Production Plan Item", "Production Plan", "po_items"),
                ("Material Request Item", "Material Request", "items"),
                ("Purchase Order Item", "Purchase Order", "items"),
                ("Purchase Receipt Item", "Purchase Receipt", "items"),
                ("Purchase Invoice Item", "Purchase Invoice", "items"),
                ("Subcontracting Order Item", "Subcontracting Order", "items"),
                ("Subcontracting Receipt Item", "Subcontracting Receipt", "items"),
                ("Stock Entry Detail", "Stock Entry", "items"),
                ("Delivery Note Item", "Delivery Note", "items"), ("Sales Invoice Item", "Sales Invoice", "items"))
    try:
        for doctype, fieldname in parents:
            rows = frappe.db.sql(f"SELECT name FROM `tab{doctype}` WHERE `{fieldname}`=%s "
                                 "AND docstatus<2 LIMIT 1 FOR UPDATE NOWAIT", (flow_name,))
            if rows:
                raise FlowQuantityError("已有有效下游单据，不能重新确认或改定计划库存数量；请先核对并处理原单据")
        for childtype, parenttype, parentfield in children:
            rows = frappe.db.sql(f"SELECT detail.name FROM `tab{childtype}` detail "
                                 f"LEFT JOIN `tab{parenttype}` parent ON parent.name=detail.parent "
                                 f"AND detail.parenttype='{parenttype}' AND detail.parentfield='{parentfield}' "
                                 "WHERE detail.jf_flow_order=%s AND "
                                 "(parent.name IS NULL OR parent.docstatus<2) "
                                 "LIMIT 1 FOR UPDATE NOWAIT", (flow_name,))
            if rows:
                raise FlowQuantityError("已有有效下游单据，不能重新确认或改定计划库存数量；请先核对并处理原单据")
    except (frappe.QueryTimeoutError, frappe.QueryDeadlockError):
        raise FlowQuantityError("相关单据正在核对，请稍后重新读取后再确认") from None
