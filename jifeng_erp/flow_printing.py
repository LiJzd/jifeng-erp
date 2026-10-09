"""Read-only, saved-document contexts for the two existing production formats.

Custom Jinja receives a complete Document rather than the native layout's
field projection. Return only readable, unmasked snapshot fields, including
parent-scoped child fields, before a format can render them.
"""

from decimal import Decimal, InvalidOperation
from urllib.parse import unquote, urlsplit

import frappe
from frappe.model import get_permitted_fields

from jifeng_erp.flow_quantity import CONFIRMATION_FIELDS, PLAN_FIELDS as QUANTITY_FIELDS, SOURCE_FIELDS, FlowQuantityError, require_stock_plan


FLOW = "Jifeng Flow Order"
PLAN = "Production Plan"
FLOW_FIELDS = {"name", "company", "item_code", "supplier_product_code", "specification", "planned_qty", "stock_uom",
               "sales_order", "online_order_id", "work_order", "requirements", "packaging", "source_qty",
               "source_uom", "source_stock_uom", "source_conversion_factor", "source_stock_qty", "source_changed",
               "source_change_note", "stock_plan_confirmation_note"}
MATERIAL_FIELDS = {"idx", "item_code", "qty_per_unit", "uom", "note"}
OPERATION_FIELDS = {"idx", "operation", "supplier", "unit_price", "note"}
PLAN_FIELDS = {"name", "company", "docstatus", "posting_date", "jf_instruction_no", "jf_instruction_date",
               "jf_instruction_weight", "jf_instruction_weight_uom", "jf_instruction_amount", "jf_instruction_remark"}
PLAN_ITEM_FIELDS = {"idx", "jf_flow_order", "sales_order", "item_code", "bom_no", "planned_qty", "stock_uom",
                    "planned_start_date", "ordered_qty", "produced_qty"}


def _columns(doctype, parenttype=None):
    allowed = set(get_permitted_fields(doctype, parenttype=parenttype, permission_type="read", ignore_virtual=True))
    if frappe.session.user != "Administrator":
        meta = frappe.get_meta(doctype)
        levels = set(meta.get_permlevel_access(permission_type="mask", parenttype=parenttype,
                                               user=frappe.session.user))
        allowed -= {df.fieldname for df in meta.fields if df.get("mask") and (df.permlevel or 0) not in levels}
    return allowed


def _saved(doc, doctype):
    if frappe.session.user == "Guest":
        frappe.throw("请登录后打印生产单据", frappe.PermissionError)
    if not doc or doc.doctype != doctype or not isinstance(doc.name, str) or not doc.name:
        frappe.throw("请先保存准确的生产单据后打印")
    source = frappe.get_doc(doctype, doc.name)
    source.check_permission("read")
    source.check_permission("print")
    return source


def _project(document, fields, allowed):
    result = {}
    for field in fields & allowed:
        value = document.get(field)
        result[field] = "" if value is None else value
    return result


def _table(document, fieldname, childtype, fields):
    meta = frappe.get_meta(document.doctype)
    df = meta.get_field(fieldname)
    if not df or df.fieldtype != "Table" or df.options != childtype:
        frappe.throw("生产打印子表来源不完整，请核对原单")
    read = set(meta.get_permlevel_access(permission_type="read", user=frappe.session.user))
    mask = set(meta.get_permlevel_access(permission_type="mask", user=frappe.session.user))
    if ((df.permlevel or 0) not in read
            or df.get("mask") and (df.permlevel or 0) not in mask and frappe.session.user != "Administrator"):
        return set(), []
    allowed = fields & _columns(childtype, document.doctype)
    result = []
    for row in document.get(fieldname) or []:
        if (row.doctype != childtype or row.parent != document.name or row.parenttype != document.doctype
                or row.parentfield != fieldname):
            frappe.throw("生产打印子表不属于当前单据，请核对原单")
        result.append(_project(row, fields, allowed))
    return allowed, result


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _currency(source, visible):
    if "company" not in visible or not source.get("company"):
        return None
    company = frappe.get_doc("Company", source.company)
    if not company.has_permission("read") or "default_currency" not in _columns("Company"):
        return None
    return company.default_currency


def _local_image(value):
    if not isinstance(value, str) or value != value.strip() or "\\" in value or any(ord(c) < 32 for c in value):
        return None
    prefix = next((p for p in ("/private/files/", "/files/") if value.startswith(p)), None)
    if not prefix:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    filename = unquote(parts.path[len(prefix):])
    if (not filename or filename in (".", "..") or any(c in filename for c in ("/", "\\", "%"))
            or any(ord(c) < 32 for c in filename) or parts.scheme or parts.netloc or parts.query or parts.fragment):
        return None
    return int(prefix == "/private/files/")


def _image(source, visible):
    # Preserve Flow.image as the printed snapshot. Never replace it with the
    # Item's current image or an external source URL.
    if "image" not in visible or "file_url" not in _columns("File"):
        return None
    value = source.get("image")
    private = _local_image(value)
    if private is None:
        return None
    bindings = [(FLOW, source.name)]
    if "item_code" in visible and source.get("item_code"):
        bindings.append(("Item", source.item_code))
    for doctype, name in bindings:
        binding = {"file_url": value, "is_private": private, "attached_to_doctype": doctype,
                   "attached_to_name": name}
        for file_id in frappe.get_all("File", filters=binding, pluck="name", order_by="name", limit_page_length=0):
            try:
                attachment = frappe.get_doc("File", file_id)
            except frappe.DoesNotExistError:
                continue
            if (any(attachment.get(field) != expected for field, expected in binding.items())
                    or attachment.get("attached_to_field") not in (None, "", "image")):
                continue
            if attachment.has_permission("read") and attachment.is_downloadable():
                return attachment.unique_url
    return None


def jifeng_flow_sheet(doc):
    source = _saved(doc, FLOW)
    visible = _columns(FLOW)
    values = _project(source, FLOW_FIELDS, visible)
    material_fields, materials = _table(source, "materials", "Jifeng Material Line", MATERIAL_FIELDS)
    operation_fields, operations = _table(source, "operations", "Jifeng Operation Line", OPERATION_FIELDS)
    plan_confirmed = False
    if (set(QUANTITY_FIELDS + SOURCE_FIELDS + CONFIRMATION_FIELDS) | {"external_order"}) <= visible:
        try:
            require_stock_plan(source, source.get("stock_uom"))
            plan_confirmed = True
        except FlowQuantityError:
            pass
    planned = _number(values.get("planned_qty")) if plan_confirmed else None
    for row in materials:
        per_unit = _number(row.get("qty_per_unit"))
        if planned is not None and per_unit is not None:
            row["planned_requirement"] = planned * per_unit
    conversion = {"source_qty", "source_uom", "source_stock_uom", "source_conversion_factor", "source_stock_qty"}
    explicit_conversion = bool(conversion <= visible and values.get("source_uom") and values.get("source_stock_uom")
                               and all(_number(values.get(field)) is not None
                                       for field in ("source_qty", "source_conversion_factor", "source_stock_qty"))
                               and _number(values.get("source_conversion_factor")) > 0)
    return {"flow": values, "material_fields": sorted(material_fields), "materials": materials,
            "operation_fields": sorted(operation_fields), "operations": operations,
            "source_conversion": explicit_conversion, "plan_confirmed": plan_confirmed, "currency": _currency(source, visible),
            "image_url": _image(source, visible)}


def jifeng_production_instruction(doc):
    source = _saved(doc, PLAN)
    visible = _columns(PLAN)
    values = _project(source, PLAN_FIELDS, visible)
    item_fields, rows = _table(source, "po_items", "Production Plan Item", PLAN_ITEM_FIELDS)
    totals = {}
    if {"stock_uom", "planned_qty"} <= item_fields:
        for row in rows:
            number = _number(row.get("planned_qty"))
            if row.get("stock_uom") and number is not None:
                totals[row["stock_uom"]] = totals.get(row["stock_uom"], Decimal(0)) + number
    return {"plan": values, "item_fields": sorted(item_fields), "rows": rows,
            "quantity_totals": sorted(totals.items()), "currency": _currency(source, visible)}
