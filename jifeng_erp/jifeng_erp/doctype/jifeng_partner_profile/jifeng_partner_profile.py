import frappe
from frappe.model.document import Document

from jifeng_erp.partner_archive import validate_profile


class JifengPartnerProfile(Document):
    def validate(self):
        validate_profile(self)

    def on_trash(self):
        frappe.throw("工厂档案保留关联及新建请求身份，请使用停用，不可删除")
