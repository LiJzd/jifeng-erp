import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from jifeng_erp.catalog_access import check_employee
from jifeng_erp.costing import CostingError, number


class JifengPlate(Document):
    def before_insert(self):
        # A schema default would fill this new column on historic rows. Apply
        # the observed initial value only to new records, retaining explicit "".
        if self.get("assessment_score") is None:
            self.assessment_score = "1"

    def validate(self):
        self.plate_no = (self.plate_no or "").strip()
        if not self.plate_no:
            frappe.throw("请填写版种编号")
        if not self.plate_date:
            frappe.throw("请填写建档日期")
        if self.item_code:
            item = frappe.get_doc("Item", self.item_code)
            item.check_permission("read")
        check_employee(self.maker, self.company, "制版人员")
        check_employee(self.modeler, self.company, "建模人员")
        try:
            number(self.reference_cost, "参考制作费用")
        except CostingError as exc:
            frappe.throw(str(exc))
        source = self.get("copy_from")
        seen = {self.name} if self.name else set()
        while source:
            if source in seen:
                frappe.throw("复制来源不能形成循环")
            seen.add(source)
            original = frappe.get_doc("Jifeng Plate", source)
            original.check_permission("read")
            if original.company != self.company:
                frappe.throw("复制来源版种必须属于同一公司")
            source = getattr(original, "copy_from", None)
        if self.retired:
            if not self.retired_date:
                frappe.throw("停用版种必须填写停用日期")
            if getdate(self.retired_date) < getdate(self.plate_date):
                frappe.throw("停用日期不能早于建档日期")
        else:
            self.retired_date = None
            self.retired_reason = None
