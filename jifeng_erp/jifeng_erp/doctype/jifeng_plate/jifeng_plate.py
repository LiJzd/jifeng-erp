import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from jifeng_erp.costing import CostingError, number


class JifengPlate(Document):
    def validate(self):
        self.plate_no = (self.plate_no or "").strip()
        if not self.plate_no:
            frappe.throw("请填写版种编号")
        if not self.plate_date:
            frappe.throw("请填写建档日期")
        if self.item_code:
            item = frappe.get_doc("Item", self.item_code)
            item.check_permission("read")
        if self.maker:
            maker = frappe.get_doc("Employee", self.maker)
            if maker.company != self.company:
                frappe.throw("制版人员必须属于当前公司")
        if self.modeler:
            modeler = frappe.get_doc("Employee", self.modeler)
            if modeler.company != self.company:
                frappe.throw("建模人员必须属于当前公司")
        try:
            number(self.reference_cost, "参考制作费用")
        except CostingError as exc:
            frappe.throw(str(exc))
        if self.retired:
            if not self.retired_date:
                frappe.throw("停用版种必须填写停用日期")
            if getdate(self.retired_date) < getdate(self.plate_date):
                frappe.throw("停用日期不能早于建档日期")
        else:
            self.retired_date = None
            self.retired_reason = None
