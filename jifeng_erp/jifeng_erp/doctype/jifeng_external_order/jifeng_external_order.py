import frappe
from frappe.model.document import Document
from jifeng_erp.workflow import create_flows
from jifeng_erp.jushuitan import record_key
from jifeng_erp.flow_quantity import FlowQuantityError, assert_no_quantity_downstream, check_quantity_fields
from jifeng_erp.archive_versions import same_version


SOURCE_FIELDS = ("source_key", "external_order", "external_line_id", "sales_order", "company", "item_code")
REQUIREMENT_FIELDS = ("source_requirements", "source_packaging", "source_requirements_attachment",
                      "requirements", "packaging", "requirements_attachment")


class JifengExternalOrder(Document):
    def validate(self):
        previous = self.get_doc_before_save()
        if not self.flags.from_sync:
            if not previous:
                frappe.throw("来源订单由聚水潭同步建立；手工订单请直接新建流程单")
            fields = ("sync_key", "scope", "company", "external_order_id", "online_order_id", "shop_id", "merchant_id", "source_status", "source_modified", "source_cursor")
            def unchanged(field):
                current, original = self.get(field), previous.get(field)
                # Native form JSON represents Datetime as text; compare the
                # exact timestamp without permitting an actual source change.
                return current == original or field == "source_modified" and same_version(original, current)
            if any(not unchanged(f) for f in fields):
                frappe.throw("源订单字段只能通过同步更新")
            def source_rows(doc):
                return [{k: r.get(k) for k in ("external_line_id", "sku_id", "item_code", "qty", "price", "source_status")} for r in doc.items]
            if source_rows(self) != source_rows(previous):
                frappe.throw("源订单明细只能通过同步更新")

    def _reload_requirements(self):
        self.check_permission("read")
        self.check_permission("write")
        self.flags.for_update = True
        if not frappe.db.get_value(self.doctype, self.name, "name", for_update=True):
            frappe.throw("来源订单已不存在，请重新读取")
        self.reload()
        self.check_permission("read")
        self.check_permission("write")
        check_quantity_fields(self.doctype, ("company", "source_status", "requirements", "packaging",
                                            "requirements_attachment", "items"))
        check_quantity_fields("Jifeng External Order Item", ("external_line_id", "item_code", "source_status",
                              "flow_order", "jf_requirements", "jf_packaging"), parenttype=self.doctype)
        line_ids = [row.external_line_id for row in self.items]
        if any(not value for value in line_ids) or len(line_ids) != len(set(line_ids)):
            frappe.throw("来源订单明细缺少唯一行标识，请核对同步来源")
        if self.source_status in ("Cancelled", "Merged", "Split"):
            frappe.throw("取消、合并或拆分的源订单不能更新流程单要求")

    def _requirements_flow(self, row):
        flow = frappe.get_doc("Jifeng Flow Order", row.flow_order, for_update=True)
        flow.check_permission("read")
        flow.check_permission("write")
        check_quantity_fields("Jifeng Flow Order", SOURCE_FIELDS)
        check_quantity_fields("Jifeng Flow Order", REQUIREMENT_FIELDS, permission_type="write")
        if (flow.external_order != self.name or flow.external_line_id != row.external_line_id
                or flow.source_key != record_key(self.name, row.external_line_id)
                or flow.sales_order or flow.company != self.company or flow.item_code != row.item_code
                or row.source_status == "Cancelled"):
            frappe.throw("关联流程单的公司、商品或准确来源行不一致，请核对后再更新要求")
        return flow

    @frappe.whitelist(methods=["POST"])
    def make_flows(self):
        # Existing flows are deliberately left alone. Updating their source
        # requirements remains an explicit action in apply_requirements().
        self._reload_requirements()
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
            flow = self._requirements_flow(row)
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

    @frappe.whitelist(methods=["POST"])
    def apply_requirements(self):
        self._reload_requirements()
        targets = []
        for row in self.items:
            if row.flow_order:
                flow = self._requirements_flow(row)
                try:
                    assert_no_quantity_downstream(flow.name)
                except FlowQuantityError:
                    frappe.throw("已有有效下游业务单据，不能覆盖流程单要求；请先核对并处理原单据")
                targets.append((row, flow))
        for row, flow in targets:
            flow.source_requirements = row.get("jf_requirements") or self.requirements or ""
            flow.source_packaging = row.get("jf_packaging") or self.packaging or ""
            flow.source_requirements_attachment = self.requirements_attachment
            flow.requirements = flow.source_requirements
            flow.packaging = flow.source_packaging
            flow.requirements_attachment = flow.source_requirements_attachment
            flow.flags.from_sync = True
            flow.save()
        return len(targets)
