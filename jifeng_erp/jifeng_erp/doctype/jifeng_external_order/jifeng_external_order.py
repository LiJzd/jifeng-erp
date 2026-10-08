import frappe
from frappe.model.document import Document
from jifeng_erp.workflow import create_flows
from jifeng_erp.jushuitan import record_key


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
        # Existing flows are deliberately left alone. Updating their source
        # requirements remains an explicit action in apply_requirements().
        existing = set()
        for row in self.items:
            source_key = record_key(self.name, row.external_line_id)
            flow_name = frappe.db.get_value("Jifeng Flow Order", {"source_key": source_key}, "name")
            if flow_name:
                existing.add(flow_name)
        created = create_flows(self)
        for row in self.items:
            if not row.flow_order or row.flow_order in existing:
                continue
            flow = frappe.get_doc("Jifeng Flow Order", row.flow_order)
            requirements = row.get("jf_requirements") or self.requirements or ""
            packaging = row.get("jf_packaging") or self.packaging or ""
            flow.source_requirements = requirements
            flow.source_packaging = packaging
            flow.source_requirements_attachment = self.requirements_attachment
            if requirements:
                flow.requirements = requirements
            if packaging:
                flow.packaging = packaging
            flow.flags.from_sync = True
            flow.save()
        return created

    @frappe.whitelist()
    def apply_requirements(self):
        self.check_permission("write")
        self.reload()
        updated = 0
        for row in self.items:
            if row.flow_order:
                flow = frappe.get_doc("Jifeng Flow Order", row.flow_order)
                flow.check_permission("write")
                flow.source_requirements = row.get("jf_requirements") or self.requirements or ""
                flow.source_packaging = row.get("jf_packaging") or self.packaging or ""
                flow.source_requirements_attachment = self.requirements_attachment
                flow.requirements = flow.source_requirements
                flow.packaging = flow.source_packaging
                flow.requirements_attachment = flow.source_requirements_attachment
                flow.flags.from_sync = True
                flow.save()
                updated += 1
        return updated
