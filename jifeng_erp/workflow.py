import frappe
from frappe.utils import add_days, cint, today

from jifeng_erp.costing import CostingError, number
from jifeng_erp.jushuitan import record_key

MATERIAL_FIELDS = ("item_code", "item_name", "uom", "qty_per_unit", "unit_price", "supplier", "note")
OPERATION_FIELDS = ("operation", "supplier", "unit_price", "note")
PROFILE_FIELDS = ("company", "item_code", "bom", "supplier_product_code", "specification", "requirements", "packaging")
BLOCKED_STATUSES = {"Cancelled", "Merged", "Split"}


def recipe_totals(doc):
    try:
        materials = sum(number(r.qty_per_unit, "单件用量") * number(r.unit_price, "物料单价") for r in doc.materials)
        operations = sum(number(r.unit_price, "加工单价") for r in doc.operations)
    except CostingError as exc:
        frappe.throw(str(exc))
    return materials, operations


def copy_recipe(target, source):
    from jifeng_erp.flow_quantity import check_quantity_fields

    source.check_permission("read")
    check_quantity_fields(source.doctype, PROFILE_FIELDS + ("materials", "operations"))
    check_quantity_fields(target.doctype, PROFILE_FIELDS + ("materials", "operations"), permission_type="write")
    copied = {}
    for table, childtype, fields in (("materials", "Jifeng Material Line", MATERIAL_FIELDS),
                                     ("operations", "Jifeng Operation Line", OPERATION_FIELDS)):
        source_table = frappe.get_meta(source.doctype).get_field(table)
        target_table = frappe.get_meta(target.doctype).get_field(table)
        if any(not df or df.fieldtype != "Table" or df.options != childtype
               for df in (source_table, target_table)):
            frappe.throw("商品档案与流程单的物料或工序表定义不一致，请核对原单")
        check_quantity_fields(childtype, fields, parenttype=source.doctype)
        check_quantity_fields(childtype, fields, permission_type="write", parenttype=target.doctype)
        rows = source.get(table) or []
        if any(row.doctype != childtype or not row.name or row.parent != source.name
               or row.parenttype != source.doctype or row.parentfield != table for row in rows):
            frappe.throw("商品档案物料或工序明细缺少准确来源，请核对原单")
        copied[table] = [{f: row.get(f) for f in fields} for row in rows]
    for table, rows in copied.items():
        target.set(table, rows)


def item_image_snapshot(item_code):
    """Copy an authorized Item attachment once; saved Flow images stay historical."""
    from jifeng_erp.flow_quantity import check_quantity_fields

    item = frappe.get_doc("Item", item_code)
    item.check_permission("read")
    try:
        check_quantity_fields("Item", ("image",))
    except frappe.PermissionError:
        return None
    if not item.get("image"):
        return None
    from jifeng_erp.product_archive import _business_image_state

    return item.image if _business_image_state(item)["state"] == "已上传" else None


def _external_source_rows(order):
    from jifeng_erp.flow_quantity import check_quantity_fields

    order.check_permission("read")
    check_quantity_fields("Jifeng External Order", ("name", "company", "source_status", "items"))
    check_quantity_fields("Jifeng External Order Item", ("name", "parent", "parenttype", "parentfield",
                          "external_line_id", "item_code", "qty", "source_status"),
                          parenttype="Jifeng External Order")
    table = frappe.get_meta("Jifeng External Order").get_field("items")
    if not table or table.fieldtype != "Table" or table.options != "Jifeng External Order Item":
        frappe.throw("外部订单明细表定义不一致，请核对来源")
    result = {}
    for row in order.items:
        if (row.doctype != "Jifeng External Order Item" or not row.name or row.parent != order.name
                or row.parenttype != order.doctype or row.parentfield != "items"
                or not row.external_line_id or row.external_line_id in result):
            frappe.throw("外部订单明细缺少唯一且准确的来源，请核对同步订单")
        result[row.external_line_id] = row
    return result


def create_flows(order):
    order.check_permission("write")
    frappe.db.get_value(order.doctype, order.name, "name", for_update=True)
    order.reload()
    _external_source_rows(order)
    from jifeng_erp.flow_quantity import check_quantity_fields

    check_quantity_fields(order.doctype, ("online_order_id", "requirements", "packaging", "requirements_attachment"))
    if order.source_status in BLOCKED_STATUSES:
        frappe.throw("取消、合并或拆分的源订单不能生成新流程单")
    created = []
    for row in order.items:
        if row.source_status == "Cancelled":
            continue
        key = record_key(order.name, row.external_line_id)
        existing = frappe.db.get_value("Jifeng Flow Order", {"source_key": key}, "name")
        if existing:
            linked = frappe.get_doc("Jifeng Flow Order", existing)
            linked.check_permission("read")
            check_quantity_fields("Jifeng Flow Order", ("source_key", "external_order", "external_line_id", "sales_order", "company"))
            if (linked.source_key != key or linked.external_order != order.name
                    or linked.external_line_id != row.external_line_id or linked.get("sales_order")
                    or linked.company != order.company):
                frappe.throw("已有流程单的公司或准确来源行冲突，请核对原单")
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


def _sales_quantity_snapshot(order, row):
    from jifeng_erp.flow_quantity import FlowQuantityError, read_sales_quantity_snapshot

    try:
        return read_sales_quantity_snapshot(order, row)
    except FlowQuantityError as exc:
        frappe.throw(str(exc))


def stock_plan_quantity(flow):
    """Use the verified inventory-unit plan at all downstream creation gates."""
    _check_sample_flow(flow)
    from jifeng_erp.flow_quantity import (
        CONFIRMATION_FIELDS, SOURCE_FIELDS, FlowQuantityError, check_quantity_fields,
        read_item_stock_uom, require_stock_plan,
    )

    try:
        check_quantity_fields("Jifeng Flow Order", (
            "name", "company", "item_code", "planned_qty", "stock_uom", "quantity_basis",
            "sales_order", "external_order",
        ) + SOURCE_FIELDS + CONFIRMATION_FIELDS)
        return require_stock_plan(flow, read_item_stock_uom(flow.item_code))
    except FlowQuantityError as exc:
        frappe.throw(str(exc))


def _sales_line_differs(flow, row, order):
    from jifeng_erp.flow_quantity import source_snapshot_changed

    return (
        source_snapshot_changed(flow, _sales_quantity_snapshot(order, row))
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
        quantity = _sales_quantity_snapshot(order, row)
        key = sales_order_flow_key(order.name, row.name)
        existing = frappe.db.get_value("Jifeng Flow Order", {"source_key": key}, "name")
        if existing:
            linked = frappe.get_doc("Jifeng Flow Order", existing)
            linked.check_permission("read")
            from jifeng_erp.flow_quantity import check_quantity_fields

            check_quantity_fields("Jifeng Flow Order", ("source_key", "sales_order", "sales_order_item", "external_order", "company"))
            if (linked.source_key != key or linked.sales_order != order.name or linked.sales_order_item != row.name
                    or linked.get("external_order") or linked.company != order.company):
                frappe.throw("流程单来源标识冲突，请检查订单明细")
            created.append(existing)
            continue
        sample_fields = None
        if row.get("jf_sample_order"):
            from jifeng_erp.sample_quotations import sample_fields_for_flow

            sample_fields = sample_fields_for_flow(order, row)
            profile = sample_fields["profile"]
        else:
            profile = frappe.db.get_value("Jifeng Product Profile", {"item_code": row.item_code}, "name")
        flow = frappe.get_doc({
            "doctype": "Jifeng Flow Order", "source_key": key,
            "sales_order": order.name, "sales_order_item": row.name,
            "company": order.company, "item_code": row.item_code, "profile": profile,
            "planned_qty": quantity["source_stock_qty"], **quantity,
            "source_item_code": row.item_code, "source_status": order.status,
            "source_requirements": row.get("jf_requirements"),
            "source_packaging": row.get("jf_packaging"),
            "source_requirements_attachment": row.get("jf_requirements_attachment"),
            "requirements": row.get("jf_requirements"), "packaging": row.get("jf_packaging"),
            "requirements_attachment": row.get("jf_requirements_attachment"),
        })
        if sample_fields is not None:
            flow.update(sample_fields)
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
            changed = _sales_line_differs(flow, row, order) or (order.status == "Closed" and flow.source_status != "Closed")
            from jifeng_erp.flow_quantity import SOURCE_FIELDS

            quantity = _sales_quantity_snapshot(order, row)
            for field in SOURCE_FIELDS:
                setattr(flow, field, quantity[field])
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
    from jifeng_erp.flow_cost_sources import receipt_costs as read_costs

    return read_costs(flow_order)


@frappe.whitelist()
def receipt_batch_candidates(flow_order):
    """Read complete posted sources before offering manual batch classification."""
    from jifeng_erp.flow_cost_sources import receipt_batch_candidates as read_batches

    return read_batches(flow_order)


@frappe.whitelist()
def material_issue_costs(flow_order):
    from jifeng_erp.flow_cost_sources import material_issue_costs as read_costs

    return read_costs(flow_order)


@frappe.whitelist()
def posted_flow_costs(flow_order):
    from jifeng_erp.flow_cost_sources import posted_flow_costs as read_costs

    return read_costs(flow_order)


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
    _check_sample_flow(flow)
    if flow.source_changed or flow.source_status in BLOCKED_STATUSES or (flow.sales_order and flow.source_status == "Closed"):
        frappe.throw("请先核对源订单变更或取消状态")
    if flow.get("external_order"):
        order = _locked_source_order("Jifeng External Order", flow.external_order)
        rows = _external_source_rows(order)
        row = rows.get(flow.external_line_id)
        if (order.company != flow.company or order.source_status in BLOCKED_STATUSES
                or not row or row.source_status == "Cancelled"
                or row.item_code != flow.item_code or number(row.qty, "来源数量") <= 0
                or number(row.qty, "来源数量") != number(flow.source_qty, "来源快照数量")
                or flow.source_key != record_key(order.name, flow.external_line_id)):
            frappe.throw("外部订单公司、明细、数量或状态已变化，请先核对流程单来源")
    if flow.sales_order:
        sales_order = _locked_source_order("Sales Order", flow.sales_order)
        sales_order.check_permission("read")
        if sales_order.company != flow.company or sales_order.docstatus != 1 or sales_order.status == "Closed":
            frappe.throw("销售订单已取消或关闭，不能生成采购申请")
        source_line = next((row for row in sales_order.items if row.name == flow.sales_order_item), None)
        if not source_line or not _sales_row_selected(source_line) or _sales_line_differs(flow, source_line, sales_order):
            frappe.throw("销售订单明细已变化，请先核对流程单来源")


def _locked_source_order(doctype, name):
    # Source saves lock the Order before linked Flows; downstream creation has
    # already locked the Flow. NOWAIT prevents a reversed wait and the native
    # for_update reload reads the current row rather than an older snapshot.
    if doctype not in ("Jifeng External Order", "Sales Order"):
        frappe.throw("流程单来源类型无效")
    try:
        rows = frappe.db.sql(f"SELECT name FROM `tab{doctype}` WHERE name=%s FOR UPDATE NOWAIT", (name,))
    except (frappe.QueryTimeoutError, frappe.QueryDeadlockError):
        frappe.throw("来源订单正在修改，请重新读取后再生成下游单据")
    if not rows:
        frappe.throw("流程单来源订单已不存在，请核对原单")
    return frappe.get_doc(doctype, name, for_update=True)


def _check_sample_flow(flow):
    """Leave ordinary flows unchanged, including sites before sample schema sync."""
    fields = ("jf_sample_order", "jf_sample_order_item", "jf_sample_source_digest",
              "jf_sample_source_snapshot", "jf_source_quotation", "jf_source_quotation_item")
    if not any(flow.get(field) for field in fields):
        if not flow.get("sales_order") or not flow.get("sales_order_item"):
            return
        if not frappe.get_meta("Sales Order Item").has_field("jf_sample_order"):
            return
    from jifeng_erp.sample_quotations import check_sample_flow_source

    check_sample_flow_source(flow, active=True, lock=True)


def make_work_order(flow):
    flow.check_permission("write")
    frappe.db.get_value(flow.doctype, flow.name, "name", for_update=True)
    flow.reload()
    validate_flow_source(flow)
    planned = stock_plan_quantity(flow)
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

    # Material Issue is a separate expense-consumption path. The native Work
    # Order transfers BOM materials into WIP and later consumes them again.
    # Do not create a new Work Order while any linked issue remains net-issued;
    # the user must first return only material that is physically unconsumed.
    from jifeng_erp.material_issue import assert_no_net_issue_for_manufacturing

    assert_no_net_issue_for_manufacturing(flow)

    work_order = frappe.get_doc({
        "doctype": "Work Order", "company": flow.company,
        "production_item": flow.item_code, "bom_no": flow.bom,
        "track_semi_finished_goods": bom.get("track_semi_finished_goods") or 0,
        "qty": float(planned), "sales_order": flow.sales_order,
        "sales_order_item": flow.sales_order_item, "jf_flow_order": flow.name,
    })
    work_order.get_items_and_operations_from_bom()
    work_order.insert()
    flow.work_order = work_order.name
    flow.save()
    return work_order.name


def make_material_request(flow):
    flow.check_permission("write")
    existing = frappe.db.get_value(flow.doctype, flow.name, "material_request", for_update=True)
    flow.reload()
    validate_flow_source(flow)
    planned = stock_plan_quantity(flow)
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
                   "qty": float(number(r.qty_per_unit, "用量") * planned),
                   "schedule_date": add_days(today(), 1), "warehouse": flow.target_warehouse,
                   "jf_flow_order": flow.name} for r in flow.materials],
    })
    request.insert()
    flow.material_request = request.name
    flow.save()
    return request.name
