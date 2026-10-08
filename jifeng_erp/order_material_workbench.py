"""Classic order material queries backed by saved flow analysis and native stock drafts."""

from collections import defaultdict
from decimal import Decimal

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import cint, getdate

from jifeng_erp.inventory_scope import decimal_number
from jifeng_erp.material_issue import _required_stock, make_material_issue


FLOW = "Jifeng Flow Order"
FLOW_FIELDS = ("company", "item_code", "planned_qty", "sales_order", "sales_order_item", "external_line_id",
               "external_order", "online_order_id", "source_status", "source_changed",
               "requirements", "specification", "bom")
MATERIAL_FIELDS = ("item_code", "uom", "qty_per_unit", "unit_price", "note")
LIMIT = 1000
WORK_ORDER_FIELDS = ("name", "company", "production_item", "bom_no", "qty", "docstatus",
                     "status", "source_warehouse", "wip_warehouse", "material_transferred_for_manufacturing",
                     "produced_qty", "skip_transfer", "from_wip_warehouse", "jf_flow_order")
WORK_ORDER_ITEM_FIELDS = ("item_code", "stock_uom", "required_qty", "transferred_qty", "consumed_qty",
                          "source_warehouse", "operation")
WORK_ORDER_PURPOSES = ("Material Transfer for Manufacture", "Manufacture", "Material Consumption for Manufacture")


def _fields(doc, fields, permission="read"):
    if frappe.session.user == "Administrator":
        return
    allowed = set(get_permitted_fields(doc.doctype, permission_type=permission, ignore_virtual=True))
    if not set(fields).issubset(allowed):
        frappe.throw("没有读取或填写订单用料所需字段的权限", frappe.PermissionError)
    mask_levels = set(doc.meta.get_permlevel_access(permission_type="mask", user=frappe.session.user))
    if any((field := doc.meta.get_field(name)) and field.get("mask")
           and (field.permlevel or 0) not in mask_levels for name in fields):
        frappe.throw("没有读取或填写订单用料所需字段的权限", frappe.PermissionError)


def _table(doc, fieldname, childtype, fields, permission="read"):
    if frappe.session.user == "Administrator":
        return
    field = doc.meta.get_field(fieldname)
    levels = set(doc.meta.get_permlevel_access(permission_type=permission))
    if permission == "read" and 0 not in levels and frappe.share.get_shared(
            doc.doctype, frappe.session.user, rights=["read"], limit=1):
        levels.add(0)
    if not (field and field.fieldtype == "Table" and field.options == childtype
            and (field.permlevel or 0) in levels):
        frappe.throw("没有读取或填写订单用料明细的权限", frappe.PermissionError)
    child_meta = frappe.get_meta(childtype)
    allowed = set(get_permitted_fields(childtype, parenttype=doc.doctype,
                                       permission_type=permission, ignore_virtual=True))
    if not set(fields).issubset(allowed):
        frappe.throw("没有读取或填写订单用料明细的权限", frappe.PermissionError)
    mask_levels = set(child_meta.get_permlevel_access(permission_type="mask", parenttype=doc.doctype,
                                                       user=frappe.session.user))
    if any((child_field := child_meta.get_field(name)) and child_field.get("mask")
           and (child_field.permlevel or 0) not in mask_levels for name in fields):
        frappe.throw("没有读取或填写订单用料明细的权限", frappe.PermissionError)


def _read(doctype, name, fields=()):
    doc = frappe.get_doc(doctype, name)
    doc.check_permission("read")
    _fields(doc, fields)
    return doc


def _flow(name, company, *, lock=False):
    doc = frappe.get_doc(FLOW, name, for_update=lock)
    doc.check_permission("read")
    _fields(doc, FLOW_FIELDS)
    _table(doc, "materials", "Jifeng Material Line", MATERIAL_FIELDS)
    if doc.company != company:
        frappe.throw("流程单必须属于当前公司")
    return doc


def _company(name):
    if frappe.session.user == "Guest" or not name:
        frappe.throw("请选择有权读取的公司", frappe.PermissionError)
    # Stock clerks have native Company selection, not full Company-profile
    # read permission. The derived business currency is used by stock reports.
    if not frappe.get_list("Company", filters={"name": name}, pluck="name", limit_page_length=1):
        frappe.throw("没有查询该公司的权限", frappe.PermissionError)
    doc = frappe.get_doc("Company", name)
    doc.check_permission("select")
    if not doc.default_currency:
        frappe.throw("公司未配置本币")
    return doc


def _source(flow):
    # External orders do not currently carry a business order date or Customer.
    # Never substitute their last-sync timestamp for an order date.
    result = {"date": str(getdate(flow.creation)), "date_basis": "流程建单日期",
              "delivery_date": "", "customer": "", "order": "", "source_doctype": ""}
    if flow.sales_order:
        order = _read("Sales Order", flow.sales_order,
                      ("company", "transaction_date", "customer", "delivery_date"))
        _table(order, "items", "Sales Order Item", ("item_code", "delivery_date"))
        row = next((row for row in order.items if row.name == flow.sales_order_item), None)
        if order.company != flow.company or not row or row.item_code != flow.item_code:
            frappe.throw("流程单与销售订单来源明细不一致，请核对来源")
        _read("Customer", order.customer)
        delivery = row.delivery_date or order.delivery_date
        result.update(date=str(getdate(order.transaction_date)), date_basis="销售订单日期",
                      delivery_date=str(getdate(delivery)) if delivery else "",
                      customer=order.customer, order=order.name, source_doctype=order.doctype)
    elif flow.external_order:
        order = _read("Jifeng External Order", flow.external_order, ("company",))
        _table(order, "items", "Jifeng External Order Item", ("external_line_id", "item_code"))
        row = next((row for row in order.items if row.external_line_id == flow.external_line_id), None)
        if order.company != flow.company or not row or row.item_code != flow.item_code:
            frappe.throw("流程单与外部订单来源明细不一致")
        result.update(order=order.name, source_doctype=order.doctype)
    return result


def _material_rows(flow, source, currency):
    _read("Item", flow.item_code, ("item_name",))
    planned = decimal_number(flow.planned_qty)
    if planned <= 0:
        frappe.throw("流程单计划数量必须大于零")
    rows = []
    for material in flow.materials or []:
        item = _read("Item", material.item_code, ("item_name", "stock_uom"))
        uom = material.uom or item.stock_uom
        _read("UOM", uom)
        per_unit, price = decimal_number(material.qty_per_unit), decimal_number(material.unit_price)
        if per_unit < 0 or price < 0:
            frappe.throw("本单物料用量和预算单价不能小于零")
        quantity = planned * per_unit
        rows.append({**source, "flow_order": flow.name, "flow_modified": str(flow.modified),
                     "product_code": flow.item_code, "material_row": material.name,
                     "material_code": item.name, "material_name": item.item_name,
                     "planned_qty": planned, "qty_per_unit": per_unit, "quantity": quantity,
                     "budget_qty": quantity, "unit_price": price, "budget_amount": quantity * price,
                     "uom": uom, "currency": currency, "note": material.note or "",
                     "source_changed": cint(flow.source_changed), "source_status": flow.source_status or ""})
    return rows


def _summarize(rows):
    # Exact units and currencies remain separate. Quantity totals across units
    # have no physical meaning; the client receives one total per unit.
    groups = {}
    for row in rows:
        key = (row["material_code"], row["uom"], row["currency"])
        if key not in groups:
            groups[key] = {"material_code": row["material_code"], "material_name": row["material_name"],
                           "uom": row["uom"], "currency": row["currency"],
                           "quantity": Decimal(0), "budget_amount": Decimal(0), "sources": []}
        target = groups[key]
        target["quantity"] += row["quantity"]
        target["budget_amount"] += row["budget_amount"]
        target["sources"].append({"flow_order": row["flow_order"], "material_row": row["material_row"],
                                  "order": row["order"], "source_doctype": row["source_doctype"],
                                  "date": row["date"], "customer": row["customer"],
                                  "date_basis": row["date_basis"],
                                  "quantity": row["quantity"], "budget_amount": row["budget_amount"]})
    return [groups[key] for key in sorted(groups)]


@frappe.whitelist()
def query(filters):
    filters = frappe.parse_json(filters)
    if not isinstance(filters, dict) or set(filters) - {
            "company", "from_date", "to_date", "specified_date", "keyword", "flow_order"}:
        frappe.throw("订单用料查询条件无效")
    company = _company(filters.get("company"))
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw("请填写开始和结束日期")
    start, end = getdate(filters["from_date"]), getdate(filters["to_date"])
    if start > end:
        frappe.throw("开始日期不能晚于结束日期")
    scope = {"company": company.name}
    if filters.get("flow_order"):
        _flow(filters["flow_order"], company.name)
        scope["name"] = filters["flow_order"]
    names = frappe.get_list(FLOW, filters=scope, pluck="name", order_by="creation desc, name desc",
                            limit_page_length=LIMIT + 1)
    if len(names) > LIMIT:
        frappe.throw("可查询流程单超过一千张，请指定流程单；不能显示不完整汇总")
    keyword = str(filters.get("keyword") or "").strip().casefold()
    rows, flows = [], []
    for name in names:
        flow = _flow(name, company.name)
        source = _source(flow)
        if cint(filters.get("specified_date", 1)) and not start <= getdate(source["date"]) <= end:
            continue
        materials = _material_rows(flow, source, company.default_currency)
        haystack = " ".join(str(value or "") for value in (
            flow.name, flow.item_code, flow.requirements, source["order"], source["customer"],
            *[r["material_code"] + " " + r["material_name"] for r in materials])).casefold()
        if keyword and keyword not in haystack:
            continue
        rows.extend(materials)
        flows.append({**source, "flow_order": flow.name, "flow_modified": str(flow.modified),
                      "product_code": flow.item_code, "planned_qty": flow.planned_qty,
                      "requirements": flow.requirements or "", "specification": flow.specification or "",
                      "material_count": len(materials), "source_changed": cint(flow.source_changed),
                      "source_status": flow.source_status or ""})
    return {"materials": rows, "flows": flows, "summary": _summarize(rows),
            "currency": company.default_currency,
            "can_export": frappe.has_permission(FLOW, "export"),
            "can_print": frappe.has_permission(FLOW, "print")}


def _conversion_sources(flow):
    """Check the actual conversion source before an internal quantity lookup."""
    for row in flow.materials or []:
        item = _read("Item", row.item_code, ("stock_uom", "variant_of"))
        unit = row.uom or item.stock_uom
        if unit == item.stock_uom:
            continue
        found = False
        for parent in (item.name, item.variant_of):
            if not parent:
                continue
            source = _read("Item", parent)
            _table(source, "uoms", "UOM Conversion Detail", ("uom", "conversion_factor"))
            if any(r.uom == unit for r in source.uoms):
                found = True
                break
        if found:
            continue
        names = frappe.db.sql("""SELECT name FROM `tabUOM Conversion Factor`
            WHERE from_uom=%s AND to_uom=%s LIMIT 1""", (unit, item.stock_uom))
        if not names:
            names = frappe.db.sql("""SELECT name FROM `tabUOM Conversion Factor`
                WHERE from_uom=%s AND to_uom=%s LIMIT 1""", (item.stock_uom, unit))
        if not names:
            pairs = frappe.db.sql("""SELECT first.name, second.name
                FROM `tabUOM Conversion Factor` first JOIN `tabUOM Conversion Factor` second
                ON first.from_uom=second.from_uom WHERE first.to_uom=%s AND second.to_uom=%s LIMIT 1""",
                                  (item.stock_uom, unit))
            names = [(name,) for pair in pairs for name in pair]
        for name, in names:
            _read("UOM Conversion Factor", name, ("from_uom", "to_uom", "value"))


def _issues(flow):
    # The cap considers ALL submitted linked rows, including manual vouchers.
    # Displaying only accessible vouchers as the total would invite over-issue.
    # Current reads remain valid after waiting for a concurrent creator's flow
    # lock, even when an earlier permission lookup opened a repeatable snapshot.
    try:
        parents = {row[0] for row in frappe.db.sql("""SELECT parent FROM `tabStock Entry Detail`
            WHERE jf_flow_order=%s AND parenttype='Stock Entry' FOR UPDATE NOWAIT""", (flow.name,))}
        parents.update(row[0] for row in frappe.db.sql("""SELECT name FROM `tabStock Entry`
            WHERE jf_material_issue_flow=%s FOR UPDATE NOWAIT""", (flow.name,)))
        # A manual voucher may contain rows from several flows. Discovery
        # holds child locks; never wait for its parent while another reader
        # holds that parent and is about to read these same children.
        for name in sorted(parents):
            frappe.db.get_value("Stock Entry", name, "name", for_update=True, wait=False)
    except (frappe.QueryTimeoutError, frappe.QueryDeadlockError):
        # Reject the whole read. Skipping busy vouchers would report a false
        # remaining quantity and could allow a second material issue.
        frappe.throw("相关领料凭证正在被核对，请稍后重新查询", frappe.ValidationError)
    quantities, vouchers = defaultdict(Decimal), []
    for name in sorted(parents):
        doc = frappe.get_doc("Stock Entry", name, for_update=True)
        if not doc.has_permission("read"):
            frappe.throw("无法完整读取相关领料凭证，请核对来源读取权限", frappe.PermissionError)
        doc.check_permission("read")
        _fields(doc, ("company", "stock_entry_type", "jf_material_issue_flow"))
        _table(doc, "items", "Stock Entry Detail",
               ("jf_flow_order", "item_code", "transfer_qty", "s_warehouse"))
        if doc.company != flow.company:
            frappe.throw("关联库存凭证与流程单公司不一致，请核对原凭证")
        if doc.stock_entry_type != "Material Issue":
            continue
        for row in doc.items:
            if row.jf_flow_order != flow.name:
                continue
            _read("Item", row.item_code, ("stock_uom",))
            if row.s_warehouse:
                _read("Warehouse", row.s_warehouse, ("company",))
            if doc.docstatus == 1:
                quantities[row.item_code] += decimal_number(row.transfer_qty)
        vouchers.append({"name": doc.name, "docstatus": doc.docstatus})
    submitted_issue_names = {
        voucher["name"] for voucher in vouchers
        if voucher["docstatus"] == 1
    }
    if submitted_issue_names:
        # Keep the workbench's balance consistent with the issue validator:
        # only source-bound, submitted returns reduce the net issued quantity.
        # The helper rechecks exact source rows, permissions, valuation, and
        # locks; drafts and cancelled returns are intentionally excluded there.
        from jifeng_erp.material_returns import submitted_return_quantities_for_flow

        returned = submitted_return_quantities_for_flow(flow.name, submitted_issue_names)
        for item_code, quantity in returned.items():
            quantities[item_code] -= quantity
            if quantities[item_code] < 0:
                frappe.throw("已提交领料退库数量超过流程单有效领料数量，不能显示剩余用料", frappe.ValidationError)
    return quantities, vouchers


def _production(flow, issued):
    """Read native Work Order requirements and their current material movement totals."""
    filters = {"jf_flow_order": flow.name, "docstatus": ["<", 2]}
    raw = frappe.get_all("Work Order", filters=filters, fields=["name"], limit_page_length=LIMIT + 1)
    raw_names = {row.name for row in raw}
    if not raw_names:
        return {"bom": flow.bom or "", "work_orders": [], "has_active_work_order": False}
    visible = frappe.get_list("Work Order", filters=filters, fields=["name"], limit_page_length=LIMIT + 1)
    visible_names = {row.name for row in visible}
    if len(raw_names) > LIMIT or raw_names != visible_names:
        frappe.throw("无法完整读取本流程生产工单，请核对来源读取权限", frappe.PermissionError)

    result = []
    for name in sorted(visible_names):
        work = frappe.get_doc("Work Order", name)
        work.check_permission("read")
        _fields(work, WORK_ORDER_FIELDS)
        if (work.company != flow.company or work.production_item != flow.item_code
                or work.bom_no != flow.bom or work.jf_flow_order != flow.name or work.docstatus == 2):
            frappe.throw("生产工单与当前流程单的公司、商品、BOM或来源不一致")
        _table(work, "required_items", "Work Order Item", WORK_ORDER_ITEM_FIELDS)

        entries_filter = {"work_order": name, "purpose": ["in", WORK_ORDER_PURPOSES], "docstatus": ["<", 2]}
        raw_entries = frappe.get_all("Stock Entry", filters=entries_filter, fields=["name"], limit_page_length=LIMIT + 1)
        visible_entries = frappe.get_list("Stock Entry", filters=entries_filter, fields=["name"], limit_page_length=LIMIT + 1)
        raw_entry_names = {row.name for row in raw_entries}
        visible_entry_names = {row.name for row in visible_entries}
        if len(raw_entry_names) > LIMIT or raw_entry_names != visible_entry_names:
            frappe.throw("无法完整读取工单库存凭证，请核对来源读取权限", frappe.PermissionError)
        sources = []
        for entry_name in sorted(visible_entry_names):
            entry = frappe.get_doc("Stock Entry", entry_name)
            entry.check_permission("read")
            _fields(entry, ("company", "work_order", "purpose", "docstatus", "is_return"))
            if entry.company != flow.company or entry.work_order != work.name or entry.purpose not in WORK_ORDER_PURPOSES:
                frappe.throw("工单库存凭证与当前工单不一致")
            sources.append({"name": entry.name, "purpose": entry.purpose, "docstatus": entry.docstatus,
                            "is_return": int(entry.get("is_return") or 0)})

        items = []
        for row in work.required_items or []:
            _read("Item", row.item_code, ("stock_uom",))
            required = decimal_number(row.required_qty)
            transferred = decimal_number(row.transferred_qty)
            consumed = decimal_number(row.consumed_qty)
            items.append({"item_code": row.item_code, "stock_uom": row.stock_uom,
                          "required_qty": required, "transferred_qty": transferred,
                          "consumed_qty": consumed,
                          "pending_transfer": Decimal(0) if work.skip_transfer else required - transferred,
                          "source_warehouse": row.source_warehouse or "",
                          "net_issue_qty": issued.get(row.item_code, Decimal(0))})
        result.append({"name": work.name, "docstatus": work.docstatus, "status": work.status or "",
                       "bom_no": work.bom_no, "planned_qty": decimal_number(work.qty),
                       "produced_qty": decimal_number(work.produced_qty),
                       "transferred_qty": decimal_number(work.material_transferred_for_manufacturing),
                       "flow_planned_qty": decimal_number(flow.planned_qty),
                       "skip_transfer": int(work.skip_transfer or 0),
                       "from_wip_warehouse": int(work.from_wip_warehouse or 0),
                       "source_warehouse": work.source_warehouse or "", "wip_warehouse": work.wip_warehouse or "",
                       "items": items, "sources": sources})
    return {"bom": flow.bom or "", "work_orders": result,
            "has_active_work_order": any(row["docstatus"] < 2 for row in result)}


@frappe.whitelist()
def detail(flow_order, company):
    _company(company)
    flow = _flow(flow_order, company, lock=True)
    _source(flow)
    # Check Item/UOM fields before the unit converter's internal DB reads.
    _material_rows(flow, {}, "")
    _conversion_sources(flow)
    issued, vouchers = _issues(flow)
    required, units = _required_stock(flow) if flow.materials else ({}, {})
    for code in set(issued) - set(required):
        required[code] = Decimal(0)
        units[code] = _read("Item", code, ("stock_uom",)).stock_uom
    production = _production(flow, issued)
    can_write_flow = flow.has_permission("write")
    return {"flow_order": flow.name, "flow_modified": str(flow.modified), "vouchers": vouchers,
            "materials": [{"item_code": code, "stock_uom": units[code], "required": qty,
                           "issued": issued[code], "remaining": qty - issued[code]}
                          for code, qty in sorted(required.items())],
            "production": production,
            "can_create_issue": can_write_flow and not production["has_active_work_order"]
                and frappe.has_permission("Stock Entry", "create"),
            "can_create_work_order": bool(flow.bom) and not production["has_active_work_order"]
                and can_write_flow and frappe.has_permission("Work Order", "create"),
            "can_open_work_order": bool(production["work_orders"])}


@frappe.whitelist(methods=["POST"])
def create_work_order(flow_order, company, flow_modified):
    """Create or reuse the native Work Order; actual stock moves remain native UI actions."""
    _company(company)
    flow = _flow(flow_order, company, lock=True)
    flow.check_permission("write")
    if str(flow.modified) != str(flow_modified):
        frappe.throw("流程单或BOM已变化，请重新查询并核对")
    if not flow.bom:
        frappe.throw("本流程未配置生产BOM，不能创建生产工单")
    name = flow.make_work_order()
    work = _read("Work Order", name, WORK_ORDER_FIELDS)
    if (work.company != company or work.jf_flow_order != flow.name
            or work.production_item != flow.item_code or work.bom_no != flow.bom):
        frappe.throw("生成的生产工单与当前流程单来源不一致")
    return {"name": name, "docstatus": work.docstatus}


@frappe.whitelist(methods=["POST"])
def create_issue(flow_order, company, flow_modified, warehouse):
    _company(company)
    frappe.has_permission("Stock Entry", "read", throw=True)
    # The native factory locks the same flow, reuses the original draft and
    # performs native insert validation. Lock BEFORE checking the UI snapshot.
    flow = _flow(flow_order, company, lock=True)
    flow.check_permission("write")
    if str(flow.modified) != str(flow_modified):
        frappe.throw("流程单用料分析已变化，请重新查询并核对")
    _source(flow)
    issued, _ = _issues(flow)
    production = _production(flow, issued)
    if production["has_active_work_order"]:
        frappe.throw("本流程已有有效生产工单。请从工单办理物料转入在制仓，不要再创建费用型直接领料。")
    _material_rows(flow, {}, "")
    _conversion_sources(flow)
    _fields(flow, ("company", "item_code", "planned_qty"))
    draft = frappe.get_doc({"doctype": "Stock Entry", "company": company,
                            "stock_entry_type": "Material Issue", "purpose": "Material Issue"})
    draft.check_permission("create")
    _fields(draft, ("company", "stock_entry_type", "jf_material_issue_flow"), "write")
    _table(draft, "items", "Stock Entry Detail",
           ("item_code", "qty", "uom", "s_warehouse", "jf_flow_order"), "write")
    name = make_material_issue(flow, warehouse)
    result = _read("Stock Entry", name, ("company", "jf_material_issue_flow", "stock_entry_type"))
    if result.docstatus != 0 or result.company != company or result.jf_material_issue_flow != flow_order:
        frappe.throw("领料草稿与当前流程单不一致")
    return {"name": result.name, "docstatus": result.docstatus}
