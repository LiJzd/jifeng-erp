import frappe
from frappe.model.document import Document

from jifeng_erp.workflow import recipe_totals


class JifengProductProfile(Document):
    def validate(self):
        self.material_unit_cost, self.processing_unit_cost = recipe_totals(self)
        self.image = frappe.db.get_value("Item", self.item_code, "image")

    @frappe.whitelist()
    def load_bom(self):
        self.check_permission("write")
        self.reload()
        bom = frappe.get_doc("BOM", self.bom)
        bom.check_permission("read")
        if bom.item != self.item_code or bom.company != self.company or bom.docstatus != 1 or not bom.is_active:
            frappe.throw("请选择本公司、本商品已提交且启用的 BOM")
        self.set("materials", [{"item_code": r.item_code, "uom": r.uom,
                              "qty_per_unit": r.qty / bom.quantity, "unit_price": r.base_rate} for r in bom.items])
        self.set("operations", [{"operation": r.operation, "unit_price": r.base_operating_cost / bom.quantity,
                               "note": frappe.utils.strip_html(r.description or "")} for r in bom.operations])
        self.save()
