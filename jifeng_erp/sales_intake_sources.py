"""Exact posted finished-product movements for a Flow; this service never posts.

This is the source check needed before defining the legacy sales-intake workflow.
Receipt quantities are historical movements, not available stock or quantities
that should be received again. Stock valuation is deliberately absent: the old
sales price and approval rules have not been established.
"""

from decimal import Decimal, ROUND_HALF_UP

import frappe

from jifeng_erp import subcontract_stage
from jifeng_erp.inventory_scope import decimal_number
from jifeng_erp.ledger_source_guard import LedgerSourceGuard, _query_state, spec, table


FLOW, SE, SCR = "Jifeng Flow Order", "Stock Entry", "Subcontracting Receipt"
WO, SCO, PO, SLE = "Work Order", "Subcontracting Order", "Purchase Order", "Stock Ledger Entry"
LIMIT = 1000
STOCK_FIELDS = {"item_code", "stock_uom", "qty", "conversion_factor", "jf_flow_order"}


def _deny():
    frappe.throw("无法完整读取成品入库来源或所需字段，请核对来源读取权限", frappe.PermissionError)


def _invalid():
    frappe.throw("成品来源的凭证、明细、公司、商品、单位或库存流水不一致，请核对原生单据")


def _specs():
    values = {
        "Company": spec({"name"}),
        "Item": spec({"stock_uom", "is_stock_item"}),
        "Warehouse": spec({"company", "is_group"}),
        "UOM": spec({"name"}),
        FLOW: spec({"company", "item_code"}),
        SE: spec({"company", "purpose", "posting_date", "work_order", "job_card", "bom_no"},
                 {"items": table("Stock Entry Detail", STOCK_FIELDS | {
                     "uom", "transfer_qty", "s_warehouse", "t_warehouse", "is_finished_item"})}),
        SCR: spec({"company", "supplier", "posting_date", "is_return", "return_against"},
                  {"items": table("Subcontracting Receipt Item", STOCK_FIELDS | {
                      "rejected_qty", "warehouse", "rejected_warehouse", "secondary_item_type", "valuation_type",
                      "reference_name", "subcontracting_order", "subcontracting_order_item", "purchase_order",
                      "purchase_order_item", "subcontracting_receipt_item", "job_card", "bom"})}),
        SLE: spec({"company", "item_code", "stock_uom", "warehouse", "actual_qty", "voucher_type",
                   "voucher_no", "voucher_detail_no", "posting_date", "is_cancelled"}),
    }
    # Reuse the existing native stage contract, including operation identity and
    # exact stage BOMs. Intermediate-stage goods cannot become finished products
    # merely because their receipt is associated with the final-product Flow.
    for doctype, fields in subcontract_stage.PARENTS.items():
        values.setdefault(doctype, spec(set()))["fields"].update(fields)
    for doctype, (field, childtype, fields) in subcontract_stage.TABLES.items():
        current = values[doctype]["tables"].get(field)
        combined = set(fields) | (current[1] if current else set())
        values[doctype]["tables"][field] = table(childtype, combined)
    return values


class _Read(LedgerSourceGuard):
    """Use the shared row/field/mask/change guard with a fixed source contract."""

    def __init__(self):
        self.specs = _specs()
        self.kind = "sales_intake_sources"
        self.user = frappe.session.user
        self.documents, self.queries, self.output_observers = {}, [], []
        self.doctypes = set(self.specs)
        self.doctypes.update(child[0] for value in self.specs.values() for child in value["tables"].values())
        # No Report or DocType is installed by this RPC. The shared guard's
        # optional Report authorization observations therefore have an empty scope.
        self.report_name = ""
        self.authorization_before = self._authorization()

    def query(self, method, doctype, **kwargs):
        if method != "get_list" or doctype not in (FLOW, WO):
            return super().query(method, doctype, **kwargs)
        # Keep existing sample-source permission hooks scoped to this exact
        # population; a large unrelated order population grants no read here.
        from jifeng_erp.sales_fulfillment_scoped_query import record_list

        raw = super().query("get_all", doctype, **kwargs)
        observation = _ScopedQuery(doctype, kwargs, raw, record_list)
        result = observation.read()
        observation.visible = _query_state(result)
        self.add_output_observer(observation)
        return result

    def names(self, doctype, filters, *, discovery=False):
        rows = self.query("get_all", doctype, filters=filters, fields=["name"],
                          order_by="name asc", limit_page_length=LIMIT + 1)
        if len(rows) > LIMIT:
            frappe.throw("相关成品来源超过一千条，请先核对原生凭证范围")
        names = {row["name"] for row in rows}
        if len(names) != len(rows):
            _invalid()
        if not discovery:
            visible = self.query("get_list", doctype, filters=filters, fields=["name"],
                                 order_by="name asc", limit_page_length=LIMIT + 1)
            if names != {row["name"] for row in visible}:
                _deny()
        return sorted(names)

    def read(self, doctype, name, *, permission="read"):
        if not name or doctype not in self.specs:
            _invalid()
        if self.names(doctype, {"name": name}) != [name]:
            _deny()
        document = frappe.get_doc(doctype, name)
        document.check_permission(permission)
        return self.document(document, permission=permission)

    def parents(self, doctype, flow_order):
        childtype = self.specs[doctype]["tables"]["items"][0]
        self._fields(childtype, {"name", "parent", "parenttype", "parentfield", "jf_flow_order"}, parenttype=doctype)
        rows = self.query("get_all", childtype,
                          filters={"jf_flow_order": flow_order},
                          fields=["name", "parent", "parenttype", "parentfield"],
                          order_by="name asc", limit_page_length=LIMIT + 1)
        if len(rows) > LIMIT:
            frappe.throw("相关成品来源超过一千条，请先核对原生凭证范围")
        if any(not row.get("parent") or row.get("parenttype") != doctype or row.get("parentfield") != "items" for row in rows):
            _invalid()
        return {row["parent"] for row in rows}


class _ScopedQuery:
    def __init__(self, doctype, kwargs, raw, reader):
        self.doctype, self.kwargs, self.raw, self.reader = doctype, kwargs, _query_state(raw), reader
        self.visible = None

    def read(self):
        return self.reader(self.doctype, filters=self.kwargs["filters"], fields=self.kwargs["fields"],
                           limit=self.kwargs["limit_page_length"], scoped=True)

    def check(self):
        if _query_state(self.read()) != self.visible:
            _deny()
        self.state()

    def state(self):
        if _query_state(frappe.get_all(self.doctype, **self.kwargs)) != self.raw:
            _deny()


def _same_quantity(actual, expected, document, field):
    precision = document.precision(field)
    step = Decimal(1).scaleb(-int(precision if precision is not None else 6))
    return actual.quantize(step, rounding=ROUND_HALF_UP) == expected.quantize(step, rounding=ROUND_HALF_UP)


def _posted(document, company):
    if document.get("company") != company or document.get("docstatus") != 1:
        _invalid()


def _row(document, name):
    return subcontract_stage.detail(document, name)


def _stock_movement(read, document, row, flow, *, quantity, warehouse, rejected=Decimal(0), rejected_warehouse=None):
    item = read.read("Item", row.item_code)
    if not item.is_stock_item or row.stock_uom != item.stock_uom or row.stock_uom != flow.stock_uom:
        _invalid()
    read.read("UOM", row.stock_uom)
    for name in {warehouse, rejected_warehouse} - {None, ""}:
        location = read.read("Warehouse", name)
        if location.company != document.company or location.is_group:
            _invalid()
    if not warehouse or rejected and (not rejected_warehouse or rejected_warehouse == warehouse):
        _invalid()
    ledgers = read.names(SLE, {"voucher_type": document.doctype, "voucher_no": document.name,
                              "voucher_detail_no": row.name, "docstatus": 1, "is_cancelled": 0})
    if not ledgers:
        _invalid()
    accepted_qty, rejected_qty = Decimal(0), Decimal(0)
    held = []
    for name in ledgers:
        entry = read.read(SLE, name)
        if (entry.company != document.company or entry.item_code != row.item_code or entry.stock_uom != row.stock_uom
                or entry.voucher_type != document.doctype or entry.voucher_no != document.name
                or entry.voucher_detail_no != row.name or entry.get("docstatus") != 1 or entry.is_cancelled
                or str(entry.posting_date) != str(document.posting_date)):
            _invalid()
        movement = decimal_number(entry.actual_qty)
        if movement == 0 or (movement > 0) != (quantity > 0):
            _invalid()
        if entry.warehouse == warehouse:
            accepted_qty += movement
        elif rejected and entry.warehouse == rejected_warehouse:
            rejected_qty += movement
        else:
            _invalid()
        held.append(entry)
    if (not _same_quantity(accepted_qty, quantity, held[0], "actual_qty")
            or not _same_quantity(rejected_qty, rejected, held[0], "actual_qty")):
        _invalid()
    return accepted_qty


def _manufacture(read, document, flow):
    if document.purpose != "Manufacture":
        _invalid()
    stage = subcontract_stage.stock_stage(document, read=read.read)
    work = read.read(WO, document.work_order) if document.work_order else None
    if work:
        _posted(work, flow.company)
        if work.jf_flow_order != flow.name or work.production_item != flow.item_code:
            _invalid()
        if work.track_semi_finished_goods and not stage:
            _invalid()
    result = []
    for row in document.get("items") or []:
        if row.is_finished_item not in (0, 1):
            _invalid()
        if not row.is_finished_item:
            continue
        if row.jf_flow_order != flow.name:
            if work and row.item_code == flow.item_code:
                _invalid()
            continue
        if stage and stage["item"] != row.item_code:
            _invalid()
        if row.item_code != flow.item_code:
            # Valid semi-finished stage output has already been checked above.
            if not stage:
                _invalid()
            continue
        quantity, factor = decimal_number(row.transfer_qty), decimal_number(row.conversion_factor)
        if (quantity <= 0 or factor <= 0 or row.s_warehouse or not row.t_warehouse
                or not _same_quantity(decimal_number(row.qty) * factor, quantity, row, "transfer_qty")):
            _invalid()
        movement = _stock_movement(read, document, row, flow, quantity=quantity, warehouse=row.t_warehouse)
        result.append(_projection(document, row, movement, row.t_warehouse, "receipt", row.uom))
    return result


def _subcontract_source(read, document, row, flow):
    order = read.read(SCO, row.subcontracting_order)
    source = _row(order, row.subcontracting_order_item)
    purchase = read.read(PO, row.purchase_order)
    service = _row(purchase, row.purchase_order_item)
    for parent in (order, purchase):
        _posted(parent, flow.company)
        if parent.supplier != document.supplier:
            _invalid()
    if (order.purchase_order != purchase.name or source.purchase_order_item != service.name
            or source.item_code != row.item_code or service.fg_item != row.item_code
            or source.jf_flow_order != flow.name or service.jf_flow_order != flow.name
            or not purchase.is_subcontracted or purchase.is_old_subcontracting_flow):
        _invalid()
    stage = subcontract_stage.procurement_stage(document, row, read=read.read)
    if bool(row.job_card) != bool(source.job_card) or bool(source.job_card) != bool(service.job_card):
        _invalid()
    if stage and stage["item"] != row.item_code:
        _invalid()
    return stage


def _subcontract(read, document, flow, original_rows):
    result = []
    for row in document.get("items") or []:
        if row.jf_flow_order != flow.name and row.subcontracting_receipt_item not in original_rows:
            continue
        if row.jf_flow_order != flow.name:
            _invalid()
        if row.secondary_item_type or row.valuation_type or row.reference_name:
            continue
        stage = _subcontract_source(read, document, row, flow)
        if row.item_code != flow.item_code:
            if not stage:
                _invalid()
            continue
        quantity, factor = decimal_number(row.qty), decimal_number(row.conversion_factor)
        rejected = decimal_number(row.rejected_qty)
        direction = "return" if document.is_return else "receipt"
        if factor <= 0 or (quantity < 0) != bool(document.is_return) and quantity != 0:
            _invalid()
        if rejected != 0 and (rejected < 0) != bool(document.is_return):
            _invalid()
        if document.is_return:
            original = read.read(SCR, document.return_against)
            source = _row(original, row.subcontracting_receipt_item)
            _posted(original, flow.company)
            if (original.is_return or original.supplier != document.supplier or any(
                    row.get(field) != source.get(field) for field in (
                        "item_code", "stock_uom", "jf_flow_order", "subcontracting_order", "subcontracting_order_item",
                        "purchase_order", "purchase_order_item"))):
                _invalid()
        elif document.return_against or row.subcontracting_receipt_item:
            _invalid()
        if quantity == 0:
            continue  # No accepted finished-product movement in this row.
        movement = _stock_movement(read, document, row, flow, quantity=quantity * factor, warehouse=row.warehouse,
                                   rejected=rejected * factor, rejected_warehouse=row.rejected_warehouse)
        # SCR has no transaction-UOM field in the inspected native schema.
        result.append(_projection(document, row, movement, row.warehouse, direction, None))
    return result


def _projection(document, row, stock_qty, warehouse, direction, document_uom):
    return {"source_doctype": document.doctype, "source_name": document.name, "source_detail": row.name,
            "source_modified": str(document.modified), "posting_date": str(document.posting_date),
            "direction": direction, "item_code": row.item_code, "flow_order": row.jf_flow_order,
            "document_qty": str(decimal_number(row.qty)), "document_uom": document_uom,
            "conversion_factor": str(decimal_number(row.conversion_factor)), "stock_uom": row.stock_uom,
            "stock_qty": str(stock_qty), "warehouse": warehouse,
            "work_order": document.get("work_order"), "job_card": document.get("job_card") or row.get("job_card"),
            "return_against": document.get("return_against"), "return_source_detail": row.get("subcontracting_receipt_item")}


@frappe.whitelist()
def read_sources(company, flow_order):
    """Return complete readable posted sources; never a second receipt or price."""
    if frappe.session.user == "Guest":
        _deny()
    if frappe.conf.get("read_from_replica"):
        frappe.throw("成品入库来源须在主库查询")
    if any(not isinstance(value, str) or not value or value != value.strip()
           or len(value) > 140 for value in (company, flow_order)):
        frappe.throw("请选择明确的公司和流程单")
    read = _Read()
    read.read("Company", company, permission="select")
    flow = read.read(FLOW, flow_order)
    if flow.company != company:
        _invalid()
    item = read.read("Item", flow.item_code)
    if not item.is_stock_item or not item.stock_uom:
        _invalid()
    # The Flow's product identity is authoritative. Item-based matching never
    # discovers provenance; the unit is retained only on this local observation.
    flow_stock_uom = item.stock_uom
    observations = []
    se_names = read.parents(SE, flow.name)
    for name in read.names(WO, {"jf_flow_order": flow.name, "docstatus": 1}):
        se_names.update(read.names(SE, {"work_order": name, "purpose": "Manufacture", "docstatus": 1}))
    if se_names:
        for name in read.names(SE, {"name": ["in", sorted(se_names)], "docstatus": 1, "purpose": "Manufacture"}):
            document = read.read(SE, name)
            _posted(document, company)
            observations.append(("manufacture", document))
    scr_names = read.parents(SCR, flow.name)
    originals = []
    if scr_names:
        originals = read.names(SCR, {"name": ["in", sorted(scr_names)], "docstatus": 1, "is_return": 0})
    if originals:
        scr_names.update(read.names(SCR, {"return_against": ["in", originals], "is_return": 1, "docstatus": 1}))
    original_rows = set()
    for name in originals:
        original = read.read(SCR, name)
        original_rows.update(row.name for row in original.get("items") or [] if row.jf_flow_order == flow.name)
    if scr_names:
        for name in read.names(SCR, {"name": ["in", sorted(scr_names)], "docstatus": 1}):
            document = read.read(SCR, name)
            _posted(document, company)
            observations.append(("subcontract", document))
    # Avoid changing a held source Document (the shared guard detects mutation).
    context = frappe._dict(name=flow.name, company=flow.company, item_code=flow.item_code, stock_uom=flow_stock_uom)
    sources = []
    for kind, document in observations:
        sources.extend(_manufacture(read, document, context) if kind == "manufacture"
                       else _subcontract(read, document, context, original_rows))
    read.finish()
    return {"company": company, "flow_order": flow.name, "flow_modified": str(flow.modified),
            "item_code": flow.item_code, "stock_uom": flow_stock_uom,
            "quantity_basis": "已提交凭证的库存变动；不代表当前可售量或待入库量",
            "stock_posting_required": False, "sales_price_rule_status": "待确认",
            "sources": sorted(sources, key=lambda row: (row["posting_date"], row["source_doctype"],
                                                        row["source_name"], row["source_detail"]))}
