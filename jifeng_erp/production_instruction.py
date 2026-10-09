"""Keep native production planning linked to its authoritative flow source."""

from collections import defaultdict
from math import isfinite

import frappe
from frappe.utils import flt, now_datetime

from jifeng_erp.costing import CostingError, number


_FACTORY_SCOPE = object()
_PLAN_CHANGED = "生产计划来源已变化，请刷新后重新安排工单"
_SUBASSEMBLY_WORK_FIELDS = {
    "name", "qty", "docstatus", "produced_qty", "process_loss_qty", "company", "production_item",
    "bom_no", "stock_uom", "production_plan", "production_plan_item", "production_plan_sub_assembly_item",
    "jf_flow_order", "sales_order", "sales_order_item", "fg_warehouse", "wip_warehouse", "source_warehouse",
    "track_semi_finished_goods",
}


def _plan_flow_ids(doc):
    return {row.get("jf_flow_order") for row in doc.get("po_items") or [] if row.get("jf_flow_order")}


def _persisted_plan_flows(name):
    rows = frappe.db.sql("""SELECT jf_flow_order AS flow FROM `tabProduction Plan Item`
        WHERE parent=%s AND parenttype='Production Plan' AND parentfield='po_items'""",
                         (name,), as_dict=True)
    return {row.flow for row in rows if row.get("flow")}


def _lock_flow_ids(names):
    for name in sorted(names):
        if not frappe.db.sql("SELECT name FROM `tabJifeng Flow Order` WHERE name=%s FOR UPDATE NOWAIT", (name,)):
            frappe.throw(_PLAN_CHANGED)


def _lock_plan_rows(name):
    if not frappe.db.sql("SELECT name FROM `tabProduction Plan` WHERE name=%s FOR UPDATE NOWAIT", (name,)):
        frappe.throw("生产计划已不存在，请重新查询")
    for child, field in (("Production Plan Item", "po_items"),
                         ("Production Plan Sub Assembly Item", "sub_assembly_items")):
        frappe.db.sql(f"""SELECT name FROM `tab{child}` WHERE parent=%s
            AND parenttype='Production Plan' AND parentfield=%s ORDER BY name FOR UPDATE NOWAIT""",
                      (name, field))


def _factory_scope(plan):
    if plan.is_new():
        frappe.throw("请先保存生产计划，再生成工单")
    # The advisory snapshot determines the lock order only. The native reload
    # below supplies authoritative parent/child values after those locks.
    names = _persisted_plan_flows(plan.name)
    _lock_flow_ids(names)
    _lock_plan_rows(plan.name)
    plan.flags.for_update = True
    plan.reload()
    _check_factory_scope(plan, names)
    return names


def _check_factory_scope(plan, names):
    plan.check_permission("read")
    if not _plan_flow_ids(plan).issubset(names):
        # Never acquire a new Flow after holding the Plan parent/children.
        frappe.throw(_PLAN_CHANGED)
    if names and (plan.docstatus != 1 or plan.status in {"Closed", "Cancelled", "Stopped"}):
        frappe.throw("只能从已提交且未关闭的生产计划安排工单")
    for name in sorted(names):
        _read_locked_flow(name, plan.company)


def _qty_precision():
    return frappe.get_precision("Work Order", "qty")


def _over_quantity(actual, maximum):
    precision = _qty_precision()
    return flt(actual, precision) > flt(maximum, precision)


def _quantity(value, label):
    try:
        result = float(number(value, label))
        if not isfinite(result):
            raise CostingError(f"{label} 超出可保存范围")
        return result
    except CostingError as exc:
        frappe.throw(str(exc))


def _flow(name, company=None):
    # Lock before reading allocations so concurrent factories cannot overschedule.
    _lock_flow_ids({name})
    return _read_locked_flow(name, company)


def _read_locked_flow(name, company=None):
    flow = frappe.get_doc("Jifeng Flow Order", name, for_update=True)
    flow.check_permission("read")
    if company and flow.company != company:
        frappe.throw("生产计划与流程单公司不一致")
    return flow


def _open_source(flow):
    if flow.get("source_changed") or flow.get("source_status") in {"Cancelled", "Merged", "Split", "Closed"}:
        frappe.throw("流程单来源已变更或关闭，请先核对来源再安排生产")
    from jifeng_erp.workflow import stock_plan_quantity

    stock_plan_quantity(flow)


def _allocated(flow_name, exclude_work=None, exclude_plan=None):
    filters = {"jf_flow_order": flow_name, "docstatus": ["<", 2]}
    if exclude_work:
        filters["name"] = ["!=", exclude_work]
    # Include hidden allocations in the guard; never return their identities.
    rows = _work_rows(filters, ["qty", "production_plan", "docstatus", "produced_qty", "process_loss_qty"])
    return sum(_committed(row) for row in rows if not exclude_plan or row.production_plan != exclude_plan)


def _work_rows(filters, fields):
    # A normal read can retain an old repeatable-read snapshot after waiting on
    # the flow lock. Locking reads observe the most recent committed allocations.
    return frappe.qb.get_query("Work Order", filters=filters, fields=fields, order_by="name asc",
                               ignore_permissions=True, for_update=True).run(as_dict=True)


def _committed(work):
    # Match native plan semantics for submitted loss, while reserving draft quantities.
    quantity = _quantity(work.qty, "工单数量")
    produced = _quantity(work.get("produced_qty"), "工单入库数量")
    lost = _quantity(work.get("process_loss_qty"), "工单损耗数量")
    if min(quantity, produced, lost) < 0:
        frappe.throw("已有工单数量、入库数量或损耗数量异常，请先核对")
    if work.docstatus == 2:
        return 0
    if work.docstatus == 1:
        return max(0, produced, quantity - lost)
    return quantity


def _plan_source_reader(doctype, name):
    from jifeng_erp.production_loss import check_loss_source_read_permissions

    document = frappe.get_doc(doctype, name)
    return check_loss_source_read_permissions(document, doctype=doctype)


def _subassembly_source(plan, row, *, arranging=True):
    """Resolve the native child through its single, linked finished-item row.

    Unlinked plans retain their native manufacturing and procurement paths.
    A linked child never receives the parent's Flow quantity or item identity.
    """
    parents = [parent for parent in plan.get("po_items") or []
               if parent.name == row.get("production_plan_item")]
    if len(parents) != 1:
        if _plan_flow_ids(plan):
            frappe.throw("子装配缺少准确父生产计划成品行，不能猜测流程来源")
        return None
    if not parents[0].get("jf_flow_order"):
        return None
    from jifeng_erp.production_loss import check_loss_source_read_permissions, read_plan_subassembly_source

    check_loss_source_read_permissions(plan, doctype="Production Plan")
    source = read_plan_subassembly_source(plan, row.name, read=_plan_source_reader, arranging=arranging)
    if arranging:
        from jifeng_erp.flow_quantity import check_quantity_fields

        check_quantity_fields("Jifeng Flow Order", {"source_changed", "source_status"})
        _open_source(source["flow"])
    return source


def _check_subassembly_work(work, plan, row):
    if (work.get("production_plan") != plan.name
            or work.get("production_plan_sub_assembly_item") != row.name
            or work.get("production_plan_item") or work.get("jf_flow_order")
            or work.get("company") != plan.company or work.get("production_item") != row.production_item
            or work.get("bom_no") != row.bom_no or work.get("stock_uom") != row.stock_uom
            or work.get("track_semi_finished_goods")
            or any((work.get(field) or "") != (row.get(field) or "")
                   for field in ("sales_order", "sales_order_item"))
            or (row.get("fg_warehouse") and work.get("fg_warehouse") != row.fg_warehouse)):
        frappe.throw("已有子装配工单与准确计划行的公司、商品、BOM、单位、仓库或销售来源不一致")


def _subassembly_remaining(plan, row, *, exclude=None):
    filters = {"production_plan": plan.name, "production_plan_sub_assembly_item": row.name,
               "docstatus": ["<", 2]}
    if exclude:
        filters["name"] = ["!=", exclude]
    works = _work_rows(filters, sorted(_SUBASSEMBLY_WORK_FIELDS))
    for work in works:
        _check_subassembly_work(work, plan, row)
    maximum = _quantity(row.qty, "子装配计划库存数量")
    committed = sum(_committed(work) for work in works)
    if maximum <= 0 or _over_quantity(committed, maximum):
        frappe.throw("有效子装配工单数量超过对应生产计划明细数量")
    return max(0, flt(maximum - committed, _qty_precision())), works


class ProductionInstructionMixin:
    def check_if_latest(self):
        # Native saves lock their Plan parent before before_validate. Serialize
        # those saves in the same Flow-first order as the original Plan factory.
        names = _plan_flow_ids(self)
        if not self.is_new():
            names.update(_persisted_plan_flows(self.name))
        _lock_flow_ids(names)
        result = super().check_if_latest()
        previous = self._doc_before_save
        if (not _plan_flow_ids(self).issubset(names)
                or (previous and not _plan_flow_ids(previous).issubset(names))):
            frappe.throw(_PLAN_CHANGED)
        return result

    @frappe.whitelist()
    def make_work_order(self):
        names = _factory_scope(self)
        self._jf_plan_factory_scope = (_FACTORY_SCOPE, self.name, names)
        try:
            # Refuse an ambiguous linked child before the native factory inserts
            # any finished-item or subassembly Work Order in this transaction.
            for row in self.get("sub_assembly_items") or []:
                if row.get("type_of_manufacturing") == "In House":
                    _subassembly_source(self, row)
            # Keep native reload, warehouse defaults, operations, requirements,
            # reservation flags, subassembly/PO factories and result messages.
            return super().make_work_order()
        finally:
            self._jf_plan_factory_scope = None
            self._jf_plan_reusable_drafts = None
            self._jf_subassembly_reusable_drafts = None

    def get_production_items(self):
        scope = getattr(self, "_jf_plan_factory_scope", None)
        if scope and scope[0] is _FACTORY_SCOPE and scope[1] == self.name:
            _check_factory_scope(self, scope[2])
        else:
            _factory_scope(self)
        result = super().get_production_items()
        reusable = []
        for row in self.po_items:
            if not row.get("jf_flow_order"):
                continue
            works = _work_rows({"production_plan": self.name, "production_plan_item": row.name,
                                "docstatus": ["<", 2]},
                               ["name", "qty", "docstatus", "produced_qty", "process_loss_qty"])
            # The native calculation counts submitted orders only. Draft orders from
            # a previous factory click also reserve this linked instruction row.
            committed = sum(_committed(work) for work in works)
            planned = _quantity(row.planned_qty, "计划数量")
            if _over_quantity(committed, planned):
                frappe.throw("有效生产工单数量超过对应生产计划明细数量")
            result[row.name]["qty"] = max(0, flt(planned - committed, _qty_precision()))
            for work in works:
                if work.docstatus != 0:
                    continue
                draft = frappe.get_doc("Work Order", work.name, for_update=True)
                if not frappe.has_permission("Work Order", "read", doc=draft):
                    continue
                # Reusing an existing draft only reveals an exact current Plan
                # source. It never resizes a draft or borrows another row.
                if (draft.docstatus != 0 or draft.get("production_plan") != self.name
                        or draft.get("production_plan_item") != row.name
                        or draft.get("production_plan_sub_assembly_item")
                        or draft.get("jf_flow_order") != row.jf_flow_order
                        or draft.company != self.company or draft.production_item != row.item_code
                        or draft.bom_no != row.bom_no or draft.stock_uom != row.stock_uom
                        or (draft.get("sales_order") or "") != (row.get("sales_order") or "")
                        or (draft.get("sales_order_item") or "") != (row.get("sales_order_item") or "")):
                    frappe.throw("已有草稿工单与生产计划来源不一致，请先核对")
                reusable.append(draft.name)
        self._jf_plan_reusable_drafts = (_FACTORY_SCOPE, tuple(reusable))
        return result

    def make_work_order_for_finished_goods(self, wo_list, default_warehouses):
        result = super().make_work_order_for_finished_goods(wo_list, default_warehouses)
        reusable = getattr(self, "_jf_plan_reusable_drafts", None)
        if reusable and reusable[0] is _FACTORY_SCOPE:
            for name in reusable[1]:
                if name not in wo_list:
                    wo_list.append(name)
        return result

    def prepare_data_for_sub_assembly_items(self, row, wo_data, pending_qty=None):
        scope = getattr(self, "_jf_plan_factory_scope", None)
        if scope and scope[0] is _FACTORY_SCOPE and scope[1] == self.name:
            _check_factory_scope(self, scope[2])
        else:
            _factory_scope(self)
        current = [child for child in self.get("sub_assembly_items") or [] if child.name == row.name]
        if len(current) != 1:
            frappe.throw("子装配生产计划行已变化，请刷新后重新安排工单")
        row = current[0]
        result = super().prepare_data_for_sub_assembly_items(row, wo_data, pending_qty)
        if _subassembly_source(self, row):
            # The native pending calculation counts submitted orders only.
            # Keep native defaults and replace only this exact child's quantity.
            wo_data["qty"], works = _subassembly_remaining(self, row)
            reusable = list(getattr(self, "_jf_subassembly_reusable_drafts", None) or [])
            for work in works:
                if work.docstatus != 0:
                    continue
                draft = frappe.get_doc("Work Order", work.name, for_update=True)
                if any(draft.get(field) != work.get(field) for field in _SUBASSEMBLY_WORK_FIELDS):
                    frappe.throw(_PLAN_CHANGED)
                if frappe.has_permission("Work Order", "read", doc=draft) and draft.name not in reusable:
                    reusable.append(draft.name)
            self._jf_subassembly_reusable_drafts = reusable
        return result

    def make_work_order_for_subassembly_items(self, wo_list, subcontracted_po, default_warehouses):
        self._jf_subassembly_reusable_drafts = []
        try:
            result = super().make_work_order_for_subassembly_items(wo_list, subcontracted_po, default_warehouses)
            for name in self._jf_subassembly_reusable_drafts:
                if name not in wo_list:
                    wo_list.append(name)
            return result
        finally:
            self._jf_subassembly_reusable_drafts = None


def _match(flow, item, bom, sales_order, sales_order_item):
    if (flow.item_code != item or flow.bom != bom
            or (flow.get("sales_order") or "") != (sales_order or "")
            or (flow.get("sales_order_item") or "") != (sales_order_item or "")):
        frappe.throw("生产明细与流程单的商品、BOM 或销售来源不一致")


def _bom(flow):
    bom = frappe.get_doc("BOM", flow.bom) if flow.bom else None
    if not bom:
        frappe.throw("所选流程单尚未指定生产 BOM")
    bom.check_permission("read")
    if bom.docstatus != 1 or not bom.is_active or bom.company != flow.company or bom.item != flow.item_code:
        frappe.throw("所选流程单的生产 BOM 未提交、已停用或来源不一致")
    return bom


def validate_work_order(doc):
    if doc.get("production_plan_sub_assembly_item"):
        if doc.get("production_plan_item") or not doc.get("production_plan"):
            frappe.throw("子装配工单必须只关联准确生产计划子装配行")
        child_hint = frappe.db.get_value("Production Plan Sub Assembly Item", doc.production_plan_sub_assembly_item,
            ["parent", "parenttype", "parentfield", "production_plan_item"], as_dict=True)
        if (not child_hint or child_hint.parent != doc.production_plan
                or child_hint.parenttype != "Production Plan" or child_hint.parentfield != "sub_assembly_items"):
            frappe.throw("子装配工单与生产计划来源明细不一致")
        names = _persisted_plan_flows(doc.production_plan)
        _lock_flow_ids(names)
        _lock_plan_rows(doc.production_plan)
        plan = frappe.get_doc("Production Plan", doc.production_plan, for_update=True)
        plan.check_permission("read")
        rows = [row for row in plan.get("sub_assembly_items") or []
                if row.name == doc.production_plan_sub_assembly_item]
        if (len(rows) != 1 or rows[0].get("production_plan_item") != child_hint.production_plan_item
                or not _plan_flow_ids(plan).issubset(names)):
            frappe.throw(_PLAN_CHANGED)
        arranging = doc.docstatus == 0 or doc.is_new() or getattr(doc, "_action", None) == "submit"
        source = _subassembly_source(plan, rows[0], arranging=arranging)
        if source:
            if source["flow"].name not in names:
                frappe.throw(_PLAN_CHANGED)
            row = source["row"]
            _check_subassembly_work(doc, plan, row)
            remaining, _works = _subassembly_remaining(plan, row, exclude=doc.name or "")
            if _quantity(doc.qty, "子装配工单库存数量") <= 0 or _over_quantity(_committed(doc), remaining):
                frappe.throw("有效子装配工单数量超过对应生产计划明细数量")
            # A child quantity is in its own stock UOM. It reserves the native
            # child Plan row, not the parent's finished-product Flow quantity.
            return
        if doc.get("jf_flow_order"):
            frappe.throw("子装配缺少准确父流程来源，不能借用终成品流程数量")
    if doc.get("production_plan_item"):
        hint = frappe.db.get_value("Production Plan Item", doc.production_plan_item,
                                  ["parent", "parenttype", "parentfield", "jf_flow_order"], as_dict=True)
        if (not hint or hint.parent != doc.get("production_plan")
                or hint.parenttype != "Production Plan" or hint.parentfield != "po_items"
                or doc.get("production_plan_sub_assembly_item")):
            frappe.throw("生产工单与生产计划来源明细不一致")
        if hint.jf_flow_order:
            # This first read is advisory only. Acquire the Flow before the
            # Plan/current row and sibling WOs, then verify the hint unchanged.
            if doc.get("jf_flow_order") and doc.jf_flow_order != hint.jf_flow_order:
                frappe.throw("生产工单流程单与生产计划来源不一致")
            _flow(hint.jf_flow_order, doc.company)
            _lock_plan_rows(hint.parent)
            plan = frappe.get_doc("Production Plan", hint.parent, for_update=True)
            plan.check_permission("read")
            matches = [row for row in plan.po_items if row.name == doc.production_plan_item]
            if len(matches) != 1 or matches[0].get("jf_flow_order") != hint.jf_flow_order:
                frappe.throw(_PLAN_CHANGED)
            source = matches[0]
            if (source.parent != plan.name or source.parenttype != "Production Plan"
                    or source.parentfield != "po_items" or source.item_code != doc.production_item
                    or source.bom_no != doc.bom_no
                    or (doc.get("stock_uom") and source.stock_uom != doc.stock_uom)
                    or (source.get("sales_order") or "") != (doc.get("sales_order") or "")
                    or (source.get("sales_order_item") or "") != (doc.get("sales_order_item") or "")
                    or source.get("item_reference")):
                frappe.throw("生产工单与生产计划来源明细不一致")
            arranging = doc.docstatus == 0 or doc.is_new() or getattr(doc, "_action", None) == "submit"
            if (plan.company != doc.company or plan.docstatus != 1
                    or (arranging and plan.status in {"Closed", "Cancelled", "Stopped"})):
                frappe.throw("只能从同公司已提交且未关闭的生产计划生成工单")
            doc.jf_flow_order = source.jf_flow_order
            works = _work_rows({"production_plan": plan.name, "production_plan_item": doc.production_plan_item,
                                "docstatus": ["<", 2], "name": ["!=", doc.name or ""]},
                               ["qty", "docstatus", "produced_qty", "process_loss_qty"])
            if _over_quantity(sum(_committed(work) for work in works) + _committed(doc),
                              _quantity(source.planned_qty, "计划数量")):
                frappe.throw("有效生产工单数量超过对应生产计划明细数量")
        else:
            item = frappe.db.get_value("Production Plan Item", doc.production_plan_item, "item_code")
            if item != doc.production_item:
                frappe.throw("生产工单与生产计划来源明细不一致")
    if not doc.get("jf_flow_order"):
        return
    flow = _flow(doc.jf_flow_order, doc.company)
    _match(flow, doc.production_item, doc.bom_no, doc.get("sales_order"), doc.get("sales_order_item"))
    # Saving existing submitted orders for native progress updates must stay possible.
    if doc.docstatus == 0 or doc.is_new() or getattr(doc, "_action", None) == "submit":
        _open_source(flow)
        _bom(flow)
    quantity = _quantity(doc.qty, "工单数量")
    if quantity <= 0 or _over_quantity(_allocated(flow.name, exclude_work=doc.name) + _committed(doc),
                                      _quantity(flow.planned_qty, "流程单计划数量")):
        frappe.throw("有效生产工单数量合计超过流程单计划数量，请核对已有工单")


def validate_plan(doc, method=None):
    weight = _quantity(doc.get("jf_instruction_weight"), "指令重量")
    _quantity(doc.get("jf_instruction_amount"), "指令参考金额")
    if weight and not doc.get("jf_instruction_weight_uom"):
        frappe.throw("填写指令重量时必须选择重量单位")
    doc.jf_instruction_currency = frappe.get_cached_value("Company", doc.company, "default_currency")
    quantities = defaultdict(float)
    flows = {}
    for row in sorted(doc.get("po_items") or [], key=lambda row: row.get("jf_flow_order") or ""):
        if not row.get("jf_flow_order"):
            continue
        flow = flows.setdefault(row.jf_flow_order, None)
        if flow is None:
            flow = flows[row.jf_flow_order] = _flow(row.jf_flow_order, doc.company)
        _match(flow, row.item_code, row.bom_no, row.get("sales_order"), row.get("sales_order_item"))
        if row.stock_uom != frappe.get_cached_value("Item", row.item_code, "stock_uom"):
            frappe.throw("关联流程单的生产计划数量必须使用商品库存单位")
        if row.get("item_reference"):
            frappe.throw("关联流程单的计划明细不能合并多个销售来源，请分别安排")
        if doc.docstatus == 0 or getattr(doc, "_action", None) == "submit":
            _open_source(flow)
            _bom(flow)
        quantities[flow.name] += _quantity(row.planned_qty, "计划数量")
    for name, quantity in quantities.items():
        if quantity <= 0 or quantity + _allocated(name, exclude_plan=doc.name) > flt(flows[name].planned_qty) + 1e-6:
            frappe.throw("本计划数量加其他有效工单数量超过流程单计划数量")


def validate_flow_allocation(doc, method=None):
    if doc.is_new():
        return
    frappe.db.get_value(doc.doctype, doc.name, "name", for_update=True)
    works = _work_rows({"jf_flow_order": doc.name, "docstatus": ["<", 2]},
                       ["qty", "bom_no", "company", "production_item", "docstatus",
                        "produced_qty", "process_loss_qty"])
    if sum(_committed(work) for work in works) > flt(doc.planned_qty) + 1e-6:
        frappe.throw("流程单计划数量不能小于已有有效工单数量")
    if any(work.bom_no != doc.bom or work.company != doc.company or work.production_item != doc.item_code
           for work in works):
        frappe.throw("已有有效生产工单，请先处理工单再变更公司、商品或生产 BOM")


@frappe.whitelist()
def flow_plan_rows(company, flow_names):
    """Return an unsaved planning proposal, using readable flows only."""
    if not frappe.has_permission("Production Plan", "create"):
        frappe.throw("无权新建生产计划", frappe.PermissionError)
    names = frappe.parse_json(flow_names)
    if not isinstance(names, list) or not 1 <= len(names) <= 100 or any(not isinstance(name, str) for name in names):
        frappe.throw("请选择 1 至 100 张流程单")
    if len(set(names)) != len(names):
        frappe.throw("流程单不能重复选择")
    if not frappe.get_list("Company", filters={"name": company}, pluck="name", limit_page_length=1):
        frappe.throw("没有安排该公司生产计划的权限", frappe.PermissionError)
    result = []
    # Sorted locking order also keeps multi-flow imports from deadlocking each other.
    for name in sorted(names):
        flow = _flow(name, company)
        _open_source(flow)
        _bom(flow)
        pending = flt(flow.planned_qty) - _allocated(name)
        if pending <= 1e-6:
            frappe.throw("所选流程单已全部安排工单，请查看已有工单")
        item = frappe.get_doc("Item", flow.item_code)
        item.check_permission("read")
        result.append({"jf_flow_order": name, "item_code": flow.item_code, "bom_no": flow.bom,
                       "planned_qty": pending, "stock_uom": item.stock_uom,
                       "description": item.description, "sales_order": flow.get("sales_order"),
                       "sales_order_item": flow.get("sales_order_item"), "include_exploded_items": 1,
                       "planned_start_date": now_datetime()})
    return result
