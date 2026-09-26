import frappe
from frappe.model.document import Document
from jifeng_erp.workflow import create_flows


class JifengExternalOrder(Document):
    def validate(self):
        previous = self.get_doc_before_save()
        if not self.flags.from_sync:
            if not previous:
                frappe.throw("来源订单由聚水潭同步建立；手工订单请直接新建流程单")
            fields = ("sync_key", "scope", "company", "external_order_id", "online_order_id", "shop_id", "merchant_id", "source_status", "source_modified", "source_cursor")
            if any(self.get(f) != previous.get(f) for f in fields):
                frappe.throw("源订单字段只能通过同步更新")
            def source_rows(doc):
                return [{k: r.get(k) for k in ("external_line_id", "sku_id", "item_code", "qty", "price", "source_status")} for r in doc.items]
            if source_rows(self) != source_rows(previous):
                frappe.throw("源订单明细只能通过同步更新")

    @frappe.whitelist()
    def make_flows(self):
        return create_flows(self)

    @frappe.whitelist()
    def apply_requirements(self):
        self.check_permission("write")
        self.reload()
        for row in self.items:
            if row.flow_order:
                flow = frappe.get_doc("Jifeng Flow Order", row.flow_order)
                flow.check_permission("write")
                flow.requirements, flow.packaging = self.requirements, self.packaging
                flow.requirements_attachment = self.requirements_attachment
                flow.save()
