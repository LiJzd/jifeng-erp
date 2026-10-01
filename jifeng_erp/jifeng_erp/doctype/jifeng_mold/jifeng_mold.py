import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from jifeng_erp.catalog_access import check_employee
from jifeng_erp.costing import CostingError, number


class JifengMold(Document):
    def before_insert(self):
        from jifeng_erp.tool_archive import before_insert_record

        before_insert_record(self)

    def before_rename(self, old, new, merge=False):
        from jifeng_erp.tool_archive import prevent_rename

        prevent_rename(self, old, new, merge)

    def validate(self):
        self.mold_no = (self.mold_no or "").strip()
        self.product_code = (self.product_code or "").strip()
        if not self.mold_no or not self.product_code:
            frappe.throw("模具号和产品号不能为空")
        if not self.mold_date:
            frappe.throw("请填写建档日期")
        if self.item_code:
            item = frappe.get_doc("Item", self.item_code)
            item.check_permission("read")
        check_employee(self.maker, self.company, "制作人员")
        try:
            if number(self.quantity, "模具数量") <= 0:
                frappe.throw("模具数量必须大于零")
            number(self.amount, "参考金额")
        except CostingError as exc:
            frappe.throw(str(exc))

        if self.scrapped:
            if not self.scrap_date:
                frappe.throw("已报废模具必须填写报废日期")
            if getdate(self.scrap_date) < getdate(self.mold_date):
                frappe.throw("报废日期不能早于建档日期")
        else:
            self.scrap_date = None
            self.scrap_reason = None

        if self.copy_from:
            seen = {self.name} if self.name else set()
            source = self.copy_from
            while source:
                if source in seen:
                    frappe.throw("复制来源不能形成循环")
                seen.add(source)
                original = frappe.get_doc("Jifeng Mold", source)
                original.check_permission("read")
                if original.company != self.company:
                    frappe.throw("复制来源模具必须属于同一公司")
                source = original.copy_from

        from jifeng_erp.tool_archive import validate_record

        validate_record(self)
