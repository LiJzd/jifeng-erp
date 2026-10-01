import frappe
from frappe.model.document import Document

from jifeng_erp.costing import CostingError, number
from jifeng_erp.workflow import recipe_totals


class JifengProductProfile(Document):
    def before_rename(self, old, new, merge=False):
        # autoname field:item_code would let native force rename rewrite the
        # frozen Item binding without going through validate().
        frappe.throw("产品档案主键不能重命名，请修改档案原文名称")

    def before_insert(self):
        from jifeng_erp.product_archive import before_insert_profile

        before_insert_profile(self)

    def validate(self):
        if not self.is_new():
            before = self.get_doc_before_save()
            if not before or any(before.get(field) != self.get(field) for field in ("company", "item_code")):
                frappe.throw("已保存产品档案的公司和业务商品不能改绑")
            if any(before.get(field) != self.get(field) for field in ("request_id", "request_digest", "creation_mode")):
                frappe.throw("产品档案新建追溯身份不能修改")
        else:
            before = None
        item = self._check_reference("Item", self.item_code, "read")
        self._check_reference("Company", self.company)
        self._check_reference("Supplier", self.supplier)
        if any(self.get(field) for field in ("request_id", "creation_mode", "category", "category_detail")) or (
                before and any(before.get(field) for field in ("category", "category_detail"))):
            from jifeng_erp.product_archive import validate_record

            validate_record(self)
        if self.get("profile_image") or self.get("patent_image"):
            from jifeng_erp.product_archive import validate_images

            validate_images(self)
        if self.bom:
            self._get_bom()
        for row in self.get("materials") or []:
            self._check_reference("Item", row.item_code, "read")
            self._check_reference("UOM", row.uom)
            self._check_reference("Supplier", row.supplier)
        for row in self.get("operations") or []:
            self._check_reference("Operation", row.operation)
            self._check_reference("Supplier", row.supplier)
        self.material_unit_cost, self.processing_unit_cost = recipe_totals(self)
        self.image = item.image if item else None
        try:
            for field, label in (
                ("list_price", "参考单价"), ("blank_price", "毛胚价"),
                ("weight", "重量"), ("silver_weight", "银重"), ("stone_weight", "石重"),
            ):
                number(self.get(field), label)
        except CostingError as exc:
            frappe.throw(str(exc))

    @staticmethod
    def _check_reference(doctype, name, permission="select"):
        if not name:
            return None
        reference = frappe.get_doc(doctype, name)
        reference.check_permission(permission)
        return reference

    def _get_bom(self):
        if not self.bom:
            frappe.throw("请选择 BOM")
        bom = self._check_reference("BOM", self.bom, "read")
        if bom.item != self.item_code or bom.company != self.company or bom.docstatus != 1 or not bom.is_active:
            frappe.throw("请选择本公司、本商品已提交且启用的 BOM")
        return bom

    @frappe.whitelist()
    def load_bom(self):
        self.check_permission("write")
        self.reload()
        bom = self._get_bom()
        self.set("materials", [{"item_code": r.item_code, "uom": r.uom,
                              "qty_per_unit": r.qty / bom.quantity, "unit_price": r.base_rate} for r in bom.items])
        self.set("operations", [{"operation": r.operation, "unit_price": r.base_operating_cost / bom.quantity,
                               "note": frappe.utils.strip_html(r.description or "")} for r in bom.operations])
        self.save()
