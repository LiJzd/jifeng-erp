import frappe
from frappe.model.document import Document

from jifeng_erp.inventory_archive import validate_profile


class JifengInventoryProfile(Document):
    def validate(self):
        validate_profile(self)

    def on_trash(self):
        frappe.throw("库存档案保留新建请求及来源身份，请使用停用，不可删除")
