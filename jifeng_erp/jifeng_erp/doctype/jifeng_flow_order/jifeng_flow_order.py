import frappe
from frappe.model.document import Document

from jifeng_erp.costing import number
from jifeng_erp.flow_quantity import (
    CONFIRMATION_FIELDS, MANUAL_BASIS, PENDING_BASIS, SOURCE_FIELDS, STOCK_BASIS,
    FlowQuantityError, assert_no_quantity_downstream, check_quantity_fields,
    plan_quantity, positive_quantity, read_item_stock_uom, read_sales_quantity_snapshot,
    require_stock_plan, same_version, unit_name,
)
from jifeng_erp.workflow import copy_recipe, item_image_snapshot, make_material_request, make_work_order, recipe_totals


_QUANTITY_CONFIRMED = object()


def _recipe_quantities(document):
    return (tuple(tuple(row.get(field) for field in ("item_code", "uom", "qty_per_unit", "unit_price"))
                  for row in document.get("materials") or []),
            tuple(tuple(row.get(field) for field in ("operation", "unit_price"))
                  for row in document.get("operations") or []))


class JifengFlowOrder(Document):
    def validate(self):
        if self.external_order and self.sales_order:
            frappe.throw("流程单不能同时关联外部订单和销售订单")
        if not self.external_order and not self.sales_order:
            self.source_key = None
        previous = self.get_doc_before_save()
        if previous and self.bom != previous.bom and previous.work_order:
            work_status = frappe.db.get_value("Work Order", previous.work_order, "docstatus")
            if work_status is not None and int(work_status) < 2:
                frappe.throw("已有有效生产工单，变更生产 BOM 前请先处理工单")
        sourced = self.external_order or self.sales_order or (previous and (previous.external_order or previous.sales_order))
        confirmed = self.flags.get("quantity_confirmed") is _QUANTITY_CONFIRMED
        if not previous and (self.get("quantity_basis") == MANUAL_BASIS
                             or any(self.get(field) not in (None, "") for field in CONFIRMATION_FIELDS)
                             or (self.external_order and self.get("quantity_basis") not in (None, "", PENDING_BASIS))):
            frappe.throw("新流程单不能预置人工核对记录，请先保存，再确认计划库存单位")
        if previous and not confirmed:
            if any(self.get(field) != previous.get(field)
                   for field in ("quantity_basis", "stock_uom") + CONFIRMATION_FIELDS):
                frappe.throw("计划库存单位和核对记录只能通过确认计划库存单位操作修改")
        if sourced and not (self.flags.from_sync or self.flags.from_sales_order):
            if not previous:
                frappe.throw("有来源订单的流程单必须从订单生成")
            protected = (
                "source_key", "external_order", "external_line_id", "online_order_id",
                "sales_order", "sales_order_item", "source_qty", "source_item_code", "source_status",
                "source_requirements", "source_packaging", "source_requirements_attachment",
                "company", "item_code",
            ) + SOURCE_FIELDS
            if confirmed:
                protected = tuple(field for field in protected if field not in SOURCE_FIELDS)
            if any(self.get(f) != previous.get(f) for f in protected):
                frappe.throw("来源身份和源单状态只能由同步更新")
            if self.source_changed != previous.source_changed and not self.flags.source_acknowledged:
                frappe.throw("请使用标记源单已核对按钮确认变更")
        try:
            if not previous and not sourced:
                stock_uom = read_item_stock_uom(self.item_code)
                if self.get("stock_uom") and self.stock_uom != stock_uom:
                    raise FlowQuantityError("手工流程单的计划单位必须是当前商品库存单位")
                self.stock_uom, self.quantity_basis = stock_uom, STOCK_BASIS
            elif not previous and self.external_order and self.get("quantity_basis") in (None, "", PENDING_BASIS):
                # The upstream order has not supplied an authoritative quantity
                # unit. Keep its numbers visible for an explicit human check.
                self.quantity_basis = PENDING_BASIS
                self.source_changed = 1
                self.source_change_note = "外部订单来源单位尚未核实，请明确确认本单计划库存单位和数量。"
            if self.get("quantity_basis") in (STOCK_BASIS, MANUAL_BASIS):
                require_stock_plan(self, read_item_stock_uom(self.item_code))
            elif previous:
                if (self.planned_qty != previous.planned_qty
                        or _recipe_quantities(self) != _recipe_quantities(previous)):
                    raise FlowQuantityError("历史流程单计划单位尚未核实，请先确认计划库存单位后修改数量或分析")
            elif not self.external_order:
                raise FlowQuantityError("来源订单数量缺少完整单位快照，请从已核对的来源生成流程单")
        except FlowQuantityError as exc:
            frappe.throw(str(exc))
        qty = number(self.planned_qty, "计划库存数量")
        if qty <= 0:
            frappe.throw("计划数量必须大于零")
        if self.get("quantity_basis") in (STOCK_BASIS, MANUAL_BASIS):
            material, processing = recipe_totals(self)
            self.material_amount, self.processing_amount = material * qty, processing * qty
        elif previous:
            # A memo edit must not recalculate a historical estimate using an
            # unproven unit, or accept a client-supplied new estimated amount.
            self.material_amount, self.processing_amount = previous.material_amount, previous.processing_amount
        else:
            self.material_amount, self.processing_amount = 0, 0
        # Later Item image uploads, source syncs and memo edits must not change
        # the picture on an existing production document. Changing the Item on
        # a manual flow deliberately establishes a new snapshot.
        self.image = (previous.get("image") if previous and previous.item_code == self.item_code
                      else item_image_snapshot(self.item_code))
        if self.target_warehouse:
            warehouse = frappe.get_doc("Warehouse", self.target_warehouse)
            warehouse.check_permission("read")
            if warehouse.company != self.company or warehouse.is_group or warehouse.disabled:
                frappe.throw("采购入库仓库必须属于当前公司且为启用的明细仓库")

    @frappe.whitelist()
    def load_profile(self):
        self.check_permission("write")
        self._reload_locked()
        self._require_stock_plan()
        profile = frappe.get_doc("Jifeng Product Profile", self.profile)
        profile.check_permission("read")
        if profile.item_code != self.item_code or profile.company != self.company:
            frappe.throw("商品档案与流程单的商品、公司必须一致")
        copy_recipe(self, profile)
        self.bom = profile.bom
        self.supplier_product_code = profile.supplier_product_code
        self.specification = profile.specification
        self.requirements = self.requirements or profile.requirements
        self.packaging = self.packaging or profile.packaging
        self.save()

    @frappe.whitelist()
    def acknowledge_source(self):
        self.check_permission("write")
        self._reload_locked()
        self._require_stock_plan()
        self.flags.source_acknowledged = True
        self.source_changed = 0
        self.source_change_note = "已人工核对源单变更；计划数量按当前流程单保留。"
        self.save()

    def _require_stock_plan(self):
        try:
            check_quantity_fields(self.doctype, ("name", "company", "item_code", "planned_qty", "stock_uom", "quantity_basis",
                                                "sales_order", "external_order") + SOURCE_FIELDS + CONFIRMATION_FIELDS)
            return require_stock_plan(self, read_item_stock_uom(self.item_code))
        except FlowQuantityError as exc:
            frappe.throw(str(exc))

    def _reload_locked(self):
        self.flags.for_update = True
        if not frappe.db.get_value(self.doctype, self.name, "name", for_update=True):
            frappe.throw("流程单已不存在，请重新查询")
        self.reload()
        self.check_permission("write")
        self.check_permission("read")

    @frappe.whitelist(methods=["GET", "POST"])
    def get_stock_plan_context(self):
        """Read saved context for the explicit dialog, without granting writes."""
        self.check_permission("read")
        if self.is_new():
            frappe.throw("请先保存流程单，再读取计划库存单位核对信息")
        stored = frappe.get_doc(self.doctype, self.name)
        stored.check_permission("read")
        check_quantity_fields(self.doctype, ("name", "modified", "item_code", "stock_uom", "planned_qty", "quantity_basis"))
        try:
            stock_uom = read_item_stock_uom(stored.item_code)
        except FlowQuantityError as exc:
            frappe.throw(str(exc))
        return {"name": stored.name, "version": str(stored.modified), "stock_uom": stock_uom,
                "current_planned_qty": stored.planned_qty, "quantity_basis": stored.get("quantity_basis")}

    @frappe.whitelist(methods=["POST"])
    def confirm_stock_plan(self, expected_version, planned_qty, stock_uom, note):
        """Explicit plan commitment; never infer or rewrite historical units."""
        self.check_permission("write")
        self.check_permission("read")
        if self.is_new():
            frappe.throw("请先保存流程单，再确认计划库存单位")
        if not isinstance(note, str) or not note.strip() or len(note) > 1000:
            frappe.throw("请填写不超过1000字的计划数量和单位核对说明")
        try:
            quantity = positive_quantity(planned_qty, "明确确认的计划库存数量")
            unit = unit_name(stock_uom)
            self.flags.for_update = True
            if not frappe.db.get_value(self.doctype, self.name, "name", for_update=True):
                frappe.throw("流程单已不存在，请重新查询")
            self.reload()
            self.check_permission("write")
            self.check_permission("read")
            if not same_version(expected_version, self.modified):
                frappe.throw("流程单已修改，请重新读取后核对计划库存数量")
            quantity = plan_quantity(planned_qty, self.precision("planned_qty"))
            check_quantity_fields(self.doctype, ("company", "item_code", "external_order", "external_line_id",
                                                "sales_order", "sales_order_item") + ("planned_qty", "stock_uom"))
            check_quantity_fields(self.doctype, ("planned_qty", "stock_uom", "quantity_basis") + CONFIRMATION_FIELDS,
                                  permission_type="write")
            if unit != read_item_stock_uom(self.item_code, for_update=True):
                raise FlowQuantityError("明确确认的单位必须是当前商品的准确库存单位")
            assert_no_quantity_downstream(self.name)
            if self.sales_order and self.external_order:
                raise FlowQuantityError("流程单不能同时关联销售订单和外部订单")
            if self.sales_order:
                self._lock_quantity_source("Sales Order", self.sales_order)
                order = frappe.get_doc("Sales Order", self.sales_order, for_update=True)
                order.check_permission("read")
                check_quantity_fields("Sales Order", ("company", "docstatus", "status", "items"))
                if order.docstatus != 1 or order.status == "Closed" or order.company != self.company:
                    raise FlowQuantityError("来源销售订单必须是同公司已提交且未关闭的订单")
                rows = [row for row in order.items if row.name == self.sales_order_item]
                if len(rows) != 1 or rows[0].item_code != self.item_code:
                    raise FlowQuantityError("来源销售明细商品或精确来源已变化，请先核对原单据")
                snapshot = read_sales_quantity_snapshot(order, rows[0])
                check_quantity_fields(self.doctype, SOURCE_FIELDS, permission_type="write")
                for field in SOURCE_FIELDS:
                    self.set(field, snapshot[field])
                basis = STOCK_BASIS
            elif self.external_order:
                self._lock_quantity_source("Jifeng External Order", self.external_order)
                order = frappe.get_doc("Jifeng External Order", self.external_order, for_update=True)
                order.check_permission("read")
                check_quantity_fields("Jifeng External Order", ("company", "source_status", "items"))
                check_quantity_fields("Jifeng External Order Item", ("name", "parent", "parenttype", "parentfield",
                                      "external_line_id", "item_code", "qty", "source_status"),
                                      parenttype="Jifeng External Order")
                table = frappe.get_meta("Jifeng External Order").get_field("items")
                if not table or table.fieldtype != "Table" or table.options != "Jifeng External Order Item":
                    raise FlowQuantityError("外部来源订单的明细表定义不一致，请核对来源")
                if order.company != self.company or order.source_status in ("Cancelled", "Merged", "Split"):
                    raise FlowQuantityError("外部来源订单已取消、拆合或公司不一致，请先核对来源")
                rows = [row for row in order.items if row.external_line_id == self.external_line_id]
                if (len(rows) != 1 or not rows[0].name or rows[0].parent != order.name
                        or rows[0].parenttype != "Jifeng External Order" or rows[0].parentfield != "items"
                        or rows[0].item_code != self.item_code
                        or rows[0].source_status == "Cancelled"):
                    raise FlowQuantityError("外部来源明细已变化，请先核对来源")
                positive_quantity(rows[0].qty, "外部来源订单数量")
                # The source has no authoritative unit fields. The human promises
                # only a plan in the Item's exact stock UOM, not an upstream factor.
                basis = MANUAL_BASIS
            else:
                basis = STOCK_BASIS
            self.planned_qty, self.stock_uom, self.quantity_basis = quantity, unit, basis
            self.stock_plan_confirmation_note = note.strip()
            self.stock_plan_confirmed_at = frappe.utils.now()
            self.stock_plan_confirmed_by = frappe.session.user
            self.flags.quantity_confirmed = _QUANTITY_CONFIRMED
            self.save()
            return {"name": self.name, "version": str(self.modified), "planned_qty": self.planned_qty,
                    "stock_uom": self.stock_uom, "quantity_basis": self.quantity_basis}
        except FlowQuantityError as exc:
            frappe.throw(str(exc))

        finally:
            if self.flags.get("quantity_confirmed") is _QUANTITY_CONFIRMED:
                self.flags.pop("quantity_confirmed", None)

    def _lock_quantity_source(self, doctype, name):
        # Native source saves may lock their parent before the Flow. NOWAIT
        # avoids waiting in that reverse order; the request must be retried only
        # after rereading. Only these two fixed source types enter this helper.
        if doctype not in ("Sales Order", "Jifeng External Order"):
            frappe.throw("计划数量核对来源类型无效")
        try:
            rows = frappe.db.sql(f"SELECT name FROM `tab{doctype}` WHERE name=%s FOR UPDATE NOWAIT", (name,))
        except (frappe.QueryTimeoutError, frappe.QueryDeadlockError):
            frappe.throw("来源订单正在被核对，请稍后重新读取再确认")
        if not rows:
            frappe.throw("计划数量核对的来源订单已不存在，请重新查询")

    @frappe.whitelist()
    def make_request(self):
        return make_material_request(self)

    @frappe.whitelist()
    def make_material_issue(self, warehouse):
        from jifeng_erp.material_issue import make_material_issue

        return make_material_issue(self, warehouse)

    @frappe.whitelist()
    def make_work_order(self):
        return make_work_order(self)

    @frappe.whitelist()
    def make_preview(self):
        self.check_permission("write")
        self._reload_locked()
        self._require_stock_plan()
        existing = frappe.db.get_value(self.doctype, self.name, "cost_preview", for_update=True)
        if existing and frappe.db.exists("Jifeng Cost Preview", existing):
            return existing
        self.reload()
        preview = frappe.get_doc({
            "doctype": "Jifeng Cost Preview", "company": self.company, "flow_order": self.name,
            "flow_order_no": self.name, "external_order_no": self.online_order_id,
            "sales_order": self.sales_order,
            "item_code": self.item_code, "planned_qty": self.planned_qty,
            "material_amount": self.material_amount, "processing_amount": self.processing_amount,
        })
        preview.insert()
        self.cost_preview = preview.name
        self.save()
        return preview.name
