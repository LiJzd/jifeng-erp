import frappe
from frappe.model.document import Document

from jifeng_erp.costing import number
from jifeng_erp.workflow import copy_recipe, make_material_request, recipe_totals


class JifengFlowOrder(Document):
    def validate(self):
        if self.external_order and self.sales_order:
            frappe.throw("流程单不能同时关联外部订单和销售订单")
        if not self.external_order and not self.sales_order:
            self.source_key = None
        previous = self.get_doc_before_save()
        sourced = self.external_order or self.sales_order or (previous and (previous.external_order or previous.sales_order))
        if sourced and not (self.flags.from_sync or self.flags.from_sales_order):
            if not previous:
                frappe.throw("有来源订单的流程单必须从订单生成")
            protected = (
                "source_key", "external_order", "external_line_id", "online_order_id",
                "sales_order", "sales_order_item", "source_qty", "source_item_code", "source_status",
                "source_requirements", "source_packaging", "source_requirements_attachment",
                "company", "item_code",
            )
            if any(self.get(f) != previous.get(f) for f in protected):
                frappe.throw("来源身份和源单状态只能由同步更新")
            if self.source_changed != previous.source_changed and not self.flags.source_acknowledged:
                frappe.throw("请使用标记源单已核对按钮确认变更")
        qty = number(self.planned_qty, "计划数量")
        if qty <= 0:
            frappe.throw("计划数量必须大于零")
        material, processing = recipe_totals(self)
        self.material_amount, self.processing_amount = material * qty, processing * qty
        self.image = frappe.db.get_value("Item", self.item_code, "image")
        if self.target_warehouse:
            warehouse = frappe.get_doc("Warehouse", self.target_warehouse)
            warehouse.check_permission("read")
            if warehouse.company != self.company or warehouse.is_group or warehouse.disabled:
                frappe.throw("采购入库仓库必须属于当前公司且为启用的明细仓库")

    @frappe.whitelist()
    def load_profile(self):
        self.check_permission("write")
        self.reload()
        profile = frappe.get_doc("Jifeng Product Profile", self.profile)
        profile.check_permission("read")
        if profile.item_code != self.item_code or profile.company != self.company:
            frappe.throw("商品档案与流程单的商品、公司必须一致")
        copy_recipe(self, profile)
        self.supplier_product_code = profile.supplier_product_code
        self.specification = profile.specification
        self.requirements = self.requirements or profile.requirements
        self.packaging = self.packaging or profile.packaging
        self.save()

    @frappe.whitelist()
    def acknowledge_source(self):
        self.check_permission("write")
        self.reload()
        self.flags.source_acknowledged = True
        self.source_changed = 0
        self.source_change_note = "已人工核对源单变更；计划数量按当前流程单保留。"
        self.save()

    @frappe.whitelist()
    def make_request(self):
        return make_material_request(self)

    @frappe.whitelist()
    def make_preview(self):
        self.check_permission("write")
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
