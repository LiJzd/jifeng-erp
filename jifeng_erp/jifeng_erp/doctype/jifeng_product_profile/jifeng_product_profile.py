import frappe
from frappe.model.document import Document

from jifeng_erp.costing import CostingError, number
from jifeng_erp.workflow import recipe_totals


class JifengProductProfile(Document):
    def validate(self):
        if not self.is_new():
            before = self.get_doc_before_save()
            if not before or any(before.get(field) != self.get(field) for field in ("company", "item_code")):
                frappe.throw("已保存产品档案的公司和业务商品不能改绑")
        item = self._check_reference("Item", self.item_code, "read")
        self._check_reference("Company", self.company)
        self._check_reference("Supplier", self.supplier)
        if self.get("profile_image"):
            # The existing archive contract requires a saved document, exact
            # private File/field binding and the caller's native File.read.
            from jifeng_erp.personnel_archive import _image

            _image(self, "profile_image", self.profile_image)
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
