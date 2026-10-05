import frappe
from frappe.model.document import Document

from jifeng_erp.finance_dictionary import before_insert_dictionary, name_dictionary, validate_dictionary


class JifengFinanceDictionary(Document):
    def before_insert(self):
        before_insert_dictionary(self)

    def autoname(self):
        name_dictionary(self)

    def validate(self):
        validate_dictionary(self)

    def before_rename(self, olddn, newdn, merge=False):
        frappe.throw("财务词典内部身份不能重命名")
