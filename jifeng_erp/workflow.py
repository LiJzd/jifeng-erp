from decimal import Decimal

import frappe
from frappe.utils import add_days, cint, today

from jifeng_erp.costing import CostingError, number, receipt_batch_quantities
from jifeng_erp.jushuitan import record_key

MATERIAL_FIELDS = ("item_code", "item_name", "uom", "qty_per_unit", "unit_price", "supplier", "note")
OPERATION_FIELDS = ("operation", "supplier", "unit_price", "note")
BLOCKED_STATUSES = {"Cancelled", "Merged", "Split"}


def recipe_totals(doc):
    try:
        materials = sum(number(r.qty_per_unit, "单件用量") * number(r.unit_price, "物料单价") for r in doc.materials)
        operations = sum(number(r.unit_price, "加工单价") for r in doc.operations)
    except CostingError as exc:
        frappe.throw(str(exc))
    return materials, operations


def copy_recipe(target, source):
    for table, fields in (("materials", MATERIAL_FIELDS), ("operations", OPERATION_FIELDS)):
        target.set(table, [{f: row.get(f) for f in fields} for row in source.get(table)])


def create_flows(order):
    order.check_permission("write")
    frappe.db.get_value(order.doctype, order.name, "name", for_update=True)
    order.reload()
    if order.source_status in BLOCKED_STATUSES:
        frappe.throw("取消、合并或拆分的源订单不能生成新流程单")
    created = []
    for row in order.items:
        if row.source_status == "Cancelled":
            continue
        key = record_key(order.name, row.external_line_id)
        existing = frappe.db.get_value("Jifeng Flow Order", {"source_key": key}, "name")
        if existing:
            row.flow_order = existing
            created.append(existing)
            continue
        if not row.item_code or number(row.qty, "订单数量") <= 0:
            frappe.throw("请先完成商品映射，并确认订单数量大于零")
        profile = frappe.db.get_value("Jifeng Product Profile", {"item_code": row.item_code}, "name")
        flow = frappe.get_doc({
            "doctype": "Jifeng Flow Order", "source_key": key, "external_order": order.name,
            "external_line_id": row.external_line_id, "online_order_id": order.online_order_id,
            "company": order.company, "item_code": row.item_code, "profile": profile,
            "planned_qty": row.qty, "source_qty": row.qty, "source_status": order.source_status,
            "requirements": order.requirements, "packaging": order.packaging,
            "requirements_attachment": order.requirements_attachment,
        })
        if profile:
            template = frappe.get_doc("Jifeng Product Profile", profile)
            template.check_permission("read")
            if template.company != order.company:
                frappe.throw("商品档案公司与订单公司不一致")
            copy_recipe(flow, template)
            flow.bom = template.bom
            flow.supplier_product_code = template.supplier_product_code
            flow.specification = template.specification
            flow.requirements = order.requirements or template.requirements
            flow.packaging = order.packaging or template.packaging
        flow.flags.from_sync = True
        flow.insert()
        row.flow_order = flow.name
        created.append(flow.name)
    order.save()
    return created


def sales_order_flow_key(order_name, row_name):
    return record_key(f"sales-order:{order_name}", row_name)


def _sales_row_selected(row):
    return bool(cint(row.get("jf_make_flow")))


def _sales_line_differs(flow, row):
    return (
        number(flow.source_qty, "源订单数量") != number(row.qty, "销售订单数量")
        or flow.source_item_code != row.item_code
        or (flow.source_requirements or "") != (row.get("jf_requirements") or "")
        or (flow.source_packaging or "") != (row.get("jf_packaging") or "")
        or (flow.source_requirements_attachment or "") != (row.get("jf_requirements_attachment") or "")
    )


@frappe.whitelist()
def create_sales_order_flows(sales_order):
    """Create one traceable flow per selected line of a submitted Sales Order."""
    order = frappe.get_doc("Sales Order", sales_order)
    order.check_permission("read")
    frappe.db.get_value("Sales Order", order.name, "name", for_update=True)
    order.reload()
    if order.docstatus != 1 or order.status == "Closed":
        frappe.throw("只能从已提交且未关闭的销售订单生成流程单")
    created = []
    for row in order.items:
        if not _sales_row_selected(row):
            continue
        if not row.item_code or number(row.qty, "销售订单数量") <= 0:
            frappe.throw("所选销售订单明细必须有商品且数量大于零")
        key = sales_order_flow_key(order.name, row.name)
        existing = frappe.db.get_value("Jifeng Flow Order", {"source_key": key}, "name")
        if existing:
            linked = frappe.get_doc("Jifeng Flow Order", existing)
            linked.check_permission("read")
            if linked.sales_order != order.name or linked.sales_order_item != row.name:
                frappe.throw("流程单来源标识冲突，请检查订单明细")
            created.append(existing)
            continue
        profile = frappe.db.get_value("Jifeng Product Profile", {"item_code": row.item_code}, "name")
        flow = frappe.get_doc({
            "doctype": "Jifeng Flow Order", "source_key": key,
            "sales_order": order.name, "sales_order_item": row.name,
            "company": order.company, "item_code": row.item_code, "profile": profile,
            "planned_qty": row.qty, "source_qty": row.qty,
            "source_item_code": row.item_code, "source_status": order.status,
            "source_requirements": row.get("jf_requirements"),
            "source_packaging": row.get("jf_packaging"),
            "source_requirements_attachment": row.get("jf_requirements_attachment"),
            "requirements": row.get("jf_requirements"), "packaging": row.get("jf_packaging"),
            "requirements_attachment": row.get("jf_requirements_attachment"),
        })
        if profile:
            template = frappe.get_doc("Jifeng Product Profile", profile)
            template.check_permission("read")
            if template.company != order.company:
                frappe.throw("商品档案公司与销售订单公司不一致")
            copy_recipe(flow, template)
            flow.bom = template.bom
            flow.supplier_product_code = template.supplier_product_code
            flow.specification = template.specification
            flow.requirements = flow.requirements or template.requirements
            flow.packaging = flow.packaging or template.packaging
        flow.flags.from_sales_order = True
        flow.insert()
        created.append(flow.name)
    if not created:
        frappe.throw("没有勾选需要生成流程单的销售订单明细")
    return created


def refresh_sales_order_flows(order, method=None):
    """Flag changed or cancelled source lines without overwriting production snapshots."""
    names = frappe.get_all("Jifeng Flow Order", filters={"sales_order": order.name}, pluck="name")
    if not names:
        return
    rows = {row.name: row for row in order.items}
    for name in names:
        flow = frappe.get_doc("Jifeng Flow Order", name)
        row = rows.get(flow.sales_order_item)
        active = order.docstatus == 1 and row and _sales_row_selected(row)
        if not active:
            flow.source_changed = 1
            flow.source_status = "Cancelled"
            flow.source_change_note = "销售订单取消或明细不再生成流程单，请核对采购、加工与回货。"
        else:
            requirements = row.get("jf_requirements") or ""
            packaging = row.get("jf_packaging") or ""
            attachment = row.get("jf_requirements_attachment") or ""
            changed = _sales_line_differs(flow, row) or (order.status == "Closed" and flow.source_status != "Closed")
            flow.source_qty = row.qty
            flow.source_item_code = row.item_code
            flow.source_status = order.status
            flow.source_requirements = requirements
            flow.source_packaging = packaging
            flow.source_requirements_attachment = attachment
            if changed:
                flow.source_changed = 1
                flow.source_change_note = "销售订单商品、数量、要求或状态已变，请核对流程单及后续单据。"
        flow.flags.from_sales_order = True
        flow.save(ignore_permissions=True)


@frappe.whitelist()
def receipt_costs(flow_order):
    flow = frappe.get_doc("Jifeng Flow Order", flow_order)
    flow.check_permission("read")
    if not frappe.has_permission("Subcontracting Receipt", "read"):
        frappe.throw("没有委外收货单读取权限", frappe.PermissionError)
    # Parent get_list respects company/user permissions; child rows alone do not.
    parents = frappe.get_list("Subcontracting Receipt", filters=[
        ["Subcontracting Receipt", "docstatus", "=", 1],
        ["Subcontracting Receipt", "company", "=", flow.company],
        ["Subcontracting Receipt Item", "jf_flow_order", "=", flow.name],
    ], pluck="name", distinct=True, limit_page_length=0)
    rows = frappe.get_all("Subcontracting Receipt Item", filters={
        "parent": ["in", parents], "parenttype": "Subcontracting Receipt",
        "jf_flow_order": flow.name, "item_code": flow.item_code,
    }, fields=["parent", "qty", "rm_cost_per_qty", "service_cost_per_qty", "additional_cost_per_qty"]) if parents else []
    result = [{
        "receipt": r.parent, "qty": r.qty,
        "material": Decimal(str(r.qty or 0)) * Decimal(str(r.rm_cost_per_qty or 0)),
        "processing": Decimal(str(r.qty or 0)) * Decimal(str(r.service_cost_per_qty or 0)),
        "other": Decimal(str(r.qty or 0)) * Decimal(str(r.additional_cost_per_qty or 0)),
    } for r in rows]
    return {"rows": result, "totals": {key: sum((r[key] for r in result), Decimal(0)) for key in ("material", "processing", "other")},
            "currency": frappe.db.get_value("Company", flow.company, "default_currency")}


@frappe.whitelist()
def receipt_batch_candidates(flow_order):
    """Offer posted receipt quantities for manual classification in a cost preview."""
    flow = frappe.get_doc("Jifeng Flow Order", flow_order)
    flow.check_permission("read")
    if not frappe.has_permission("Subcontracting Receipt", "read"):
        frappe.throw("没有委外收货单读取权限", frappe.PermissionError)
    parents = frappe.get_list("Subcontracting Receipt", filters=[
        ["Subcontracting Receipt", "docstatus", "=", 1],
        ["Subcontracting Receipt", "company", "=", flow.company],
        ["Subcontracting Receipt", "is_return", "=", 0],
        ["Subcontracting Receipt Item", "jf_flow_order", "=", flow.name],
    ], fields=["name", "posting_date"], distinct=True,
        order_by="posting_date asc, name asc", limit_page_length=0)
    if not parents:
        return []
    details = frappe.get_all("Subcontracting Receipt Item", filters={
        "parent": ["in", [parent.name for parent in parents]],
        "parenttype": "Subcontracting Receipt", "jf_flow_order": flow.name,
        "item_code": flow.item_code,
    }, fields=["name", "parent", "idx", "item_code", "qty", "rejected_qty", "process_loss_qty"])
    originals = {detail.name: detail for detail in details}
    return_filters = {"company": flow.company, "docstatus": 1, "is_return": 1,
                      "return_against": ["in", [parent.name for parent in parents]]}
    returns = frappe.get_list("Subcontracting Receipt", filters=return_filters,
                              fields=["name", "return_against"], limit_page_length=0)
    if len(returns) != frappe.db.count("Subcontracting Receipt", return_filters):
        frappe.throw("无法完整读取来源单据的退货记录，请核对退货读取权限", frappe.PermissionError)
    return_parents = {parent.name: parent.return_against for parent in returns}
    return_details = frappe.get_all("Subcontracting Receipt Item", filters={
        "parent": ["in", list(return_parents)], "parenttype": "Subcontracting Receipt",
    }, fields=["parent", "subcontracting_receipt_item", "item_code", "jf_flow_order", "qty", "rejected_qty"]) if returns and originals else []
    returned = {name: Decimal(0) for name in originals}
    for detail in return_details:
        if detail.subcontracting_receipt_item not in originals:
            if not detail.subcontracting_receipt_item and detail.item_code == flow.item_code and detail.jf_flow_order in (None, "", flow.name):
                frappe.throw("委外退货缺少来源收货明细，无法计算净回货数量，请先核对")
            continue
        source = originals[detail.subcontracting_receipt_item]
        qty, rejected = Decimal(str(detail.qty or 0)), Decimal(str(detail.rejected_qty or 0))
        if return_parents[detail.parent] != source.parent or detail.item_code != source.item_code or qty > 0 or rejected > 0:
            frappe.throw("关联委外退货的商品、来源或数量异常，请先核对")
        returned[source.name] -= qty + rejected
    order = {parent.name: index for index, parent in enumerate(parents)}
    rows = []
    for detail in sorted(details, key=lambda row: (order[row.parent], row.idx)):
        try:
            quantities = receipt_batch_quantities(detail, returned[detail.name])
        except CostingError as exc:
            frappe.throw(f"委外收货 {detail.parent}：{exc}")
        rows.append({
            "receipt": detail.parent, "receipt_item": detail.name,
            "company": flow.company, "item_code": flow.item_code,
            **quantities,
        })
    return rows


@frappe.whitelist()
def material_issue_costs(flow_order):
    """Read posted Material Issue value from its stock ledger rows, not recipe prices."""
    flow = frappe.get_doc("Jifeng Flow Order", flow_order)
    flow.check_permission("read")
    if not frappe.has_permission("Stock Entry", "read"):
        frappe.throw("没有物料出库单读取权限", frappe.PermissionError)
    parents = frappe.get_list("Stock Entry", filters=[
        ["Stock Entry", "docstatus", "=", 1],
        ["Stock Entry", "company", "=", flow.company],
        ["Stock Entry", "stock_entry_type", "=", "Material Issue"],
        ["Stock Entry Detail", "jf_flow_order", "=", flow.name],
    ], pluck="name", distinct=True, limit_page_length=0)
    details = frappe.get_all("Stock Entry Detail", filters={
        "parent": ["in", parents], "parenttype": "Stock Entry", "jf_flow_order": flow.name,
    }, fields=["name", "parent", "item_code"]) if parents else []
    by_name = {row.name: row for row in details}
    ledger = (frappe.get_all("Stock Ledger Entry", filters={
        "voucher_type": "Stock Entry", "voucher_no": ["in", parents],
        "voucher_detail_no": ["in", list(by_name)], "is_cancelled": 0,
    }, fields=["voucher_no", "voucher_detail_no", "item_code", "actual_qty", "stock_value_difference"])
        if by_name else [])
    rows = []
    for entry in ledger:
        detail = by_name.get(entry.voucher_detail_no)
        if not detail or detail.parent != entry.voucher_no or detail.item_code != entry.item_code:
            continue
        rows.append({
            "issue": entry.voucher_no, "item_code": entry.item_code,
            "qty": -Decimal(str(entry.actual_qty or 0)),
            "material": -Decimal(str(entry.stock_value_difference or 0)),
        })
    return {"rows": rows, "totals": {"material": sum((row["material"] for row in rows), Decimal(0))},
            "currency": frappe.db.get_value("Company", flow.company, "default_currency")}


@frappe.whitelist()
def posted_flow_costs(flow_order):
    """Keep direct issues and subcontracting receipt material costs as separate sources."""
    issues = material_issue_costs(flow_order)
    receipts = receipt_costs(flow_order)
    return {
        "issues": issues["rows"], "receipts": receipts["rows"],
        "totals": {
            "issue_material": issues["totals"]["material"],
            "receipt_material": receipts["totals"]["material"],
            "material": issues["totals"]["material"] + receipts["totals"]["material"],
            "processing": receipts["totals"]["processing"],
            "other": receipts["totals"]["other"],
        },
        "currency": issues["currency"],
    }


@frappe.whitelist()
def stock_availability(flow_order):
    flow = frappe.get_doc("Jifeng Flow Order", flow_order)
    flow.check_permission("read")
    if not frappe.has_permission("Stock Entry", "read"):
        frappe.throw("没有库存读取权限", frappe.PermissionError)
    warehouses = frappe.get_list("Warehouse", filters={"company": flow.company, "is_group": 0}, pluck="name", limit_page_length=0)
    items = list({row.item_code for row in flow.materials})
    bins = frappe.get_all("Bin", filters={"warehouse": ["in", warehouses], "item_code": ["in", items]},
                         fields=["item_code", "warehouse", "actual_qty", "projected_qty"]) if warehouses and items else []
    return [{**row, "stock_uom": frappe.db.get_value("Item", row.item_code, "stock_uom")} for row in bins]


def validate_flow_source(flow):
    if flow.source_changed or flow.source_status in BLOCKED_STATUSES or (flow.sales_order and flow.source_status == "Closed"):
        frappe.throw("请先核对源订单变更或取消状态")
    if flow.sales_order:
        sales_order = frappe.get_doc("Sales Order", flow.sales_order)
        sales_order.check_permission("read")
        if sales_order.docstatus != 1 or sales_order.status == "Closed":
            frappe.throw("销售订单已取消或关闭，不能生成采购申请")
        source_line = next((row for row in sales_order.items if row.name == flow.sales_order_item), None)
        if not source_line or not _sales_row_selected(source_line) or _sales_line_differs(flow, source_line):
            frappe.throw("销售订单明细已变化，请先核对流程单来源")


def make_work_order(flow):
    flow.check_permission("write")
    frappe.db.get_value(flow.doctype, flow.name, "name", for_update=True)
    flow.reload()
    validate_flow_source(flow)
    if not flow.bom:
        frappe.throw("请先选择已提交且启用的生产 BOM")
    bom = frappe.get_doc("BOM", flow.bom)
    bom.check_permission("read")
    if bom.item != flow.item_code or bom.company != flow.company or bom.docstatus != 1 or not bom.is_active:
        frappe.throw("生产 BOM 必须属于本公司、本商品且已提交并启用")

    names = frappe.get_all("Work Order", filters={"jf_flow_order": flow.name, "docstatus": ["<", 2]},
                           pluck="name", limit=2)
    if len(names) > 1:
        frappe.throw("同一流程单已有多张有效生产工单，请先核对")
    if names:
        work_order = frappe.get_doc("Work Order", names[0])
        work_order.check_permission("read")
        if (work_order.company != flow.company or work_order.production_item != flow.item_code
                or work_order.bom_no != flow.bom or work_order.sales_order != flow.sales_order
                or work_order.sales_order_item != flow.sales_order_item):
            frappe.throw("已有生产工单与流程单来源或 BOM 不一致，请先核对")
        if flow.work_order != work_order.name:
            flow.work_order = work_order.name
            flow.save()
        return work_order.name

    work_order = frappe.get_doc({
        "doctype": "Work Order", "company": flow.company,
        "production_item": flow.item_code, "bom_no": flow.bom,
        "qty": flow.planned_qty, "sales_order": flow.sales_order,
        "sales_order_item": flow.sales_order_item, "jf_flow_order": flow.name,
    })
    work_order.insert()
    flow.work_order = work_order.name
    flow.save()
    return work_order.name


def make_material_request(flow):
    flow.check_permission("write")
    existing = frappe.db.get_value(flow.doctype, flow.name, "material_request", for_update=True)
    flow.reload()
    validate_flow_source(flow)
    if existing and frappe.db.exists("Material Request", existing):
        request = frappe.get_doc("Material Request", existing)
        request.check_permission("read")
        if request.docstatus != 2:
            if request.company != flow.company or request.material_request_type != "Purchase":
                frappe.throw("已有采购申请与流程单公司或申请类型不一致，请先核对")
            if not request.items or any(row.get("jf_flow_order") != flow.name for row in request.items):
                frappe.throw("已有采购申请明细未全部关联当前流程单，请先核对")
            return existing
    if not flow.materials:
        frappe.throw("请先完成本单物料分析")
    if not flow.target_warehouse:
        frappe.throw("请先选择采购入库仓库")
    if any(number(r.qty_per_unit, "单件用量") <= 0 for r in flow.materials):
        frappe.throw("生成采购申请前，各物料单件用量必须大于零")
    request = frappe.get_doc({
        "doctype": "Material Request", "material_request_type": "Purchase",
        "company": flow.company, "schedule_date": add_days(today(), 1),
        "items": [{"item_code": r.item_code, "uom": r.uom,
                   "qty": float(number(r.qty_per_unit, "用量") * number(flow.planned_qty, "计划数量")),
                   "schedule_date": add_days(today(), 1), "warehouse": flow.target_warehouse,
                   "jf_flow_order": flow.name} for r in flow.materials],
    })
    request.insert()
    flow.material_request = request.name
    flow.save()
    return request.name
