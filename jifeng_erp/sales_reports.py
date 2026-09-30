"""Readable invoice transactions and posted sales, with exact POS mirror provenance.

Invoice payment rows describe tender on the invoice. They do not include later
Payment Entries and are not an account balance or a cash general ledger.
"""

from decimal import Decimal

import frappe
from frappe.utils import cint

from jifeng_erp.inventory_scope import column, decimal_number, readable_documents, report_scope


SI = "Sales Invoice"
POS = "POS Invoice"
STATUS_LABELS = {
    "Draft": "草稿", "Submitted": "已提交", "Paid": "已付", "Partly Paid": "部分已付",
    "Unpaid": "未付", "Overdue": "逾期未付", "Return": "退货", "Cancelled": "已取消",
    "Consolidated": "已合并", "Credit Note Issued": "已开贷项", "Internal Transfer": "内部转移",
    "Unpaid and Discounted": "未付，已贴现", "Overdue and Discounted": "逾期，已贴现",
    "Partly Paid and Discounted": "部分已付，已贴现",
}


def _scope(filters):
    if frappe.session.user == "Guest":
        frappe.throw("请先登录后查询销售台账", frappe.PermissionError)
    filters, start, end = report_scope(filters, (SI,))
    if any(filters.get(field) for field in ("item_code", "warehouse", "flow_order")):
        frappe.throw("销售凭证台账每张凭证一行，不支持商品、仓库或流程单筛选")
    if filters.get("owner") and filters.get("cashier") and filters.owner != filters.cashier:
        frappe.throw("凭证创建人查询条件不一致")
    filters.owner = filters.get("cashier") or filters.get("owner")
    types = {"销售发票": SI, "零售凭证": POS, SI: SI, POS: POS}
    if filters.get("source_type") and filters.source_type not in types:
        frappe.throw("请选择销售发票或零售凭证来源类型")
    filters.source_type = types.get(filters.get("source_type"))
    if filters.get("pos_profile"):
        # Native Sales Manager has Profile select, not read. Selection permits
        # only its name/company; no payment or account configuration is returned.
        if not (frappe.has_permission("POS Profile", "read", doc=filters.pos_profile)
                or frappe.has_permission("POS Profile", "select", doc=filters.pos_profile)):
            frappe.throw("没有选择该收银配置的权限", frappe.PermissionError)
        profiles = frappe.get_list("POS Profile", filters={
            "name": filters.pos_profile, "company": filters.company}, fields=["name", "company"],
            limit_page_length=1)
        if not profiles:
            frappe.throw("所选收银配置不存在、不属于该公司或不可选择")
    for field, doctype in (("customer", "Customer"),):
        if not filters.get(field):
            continue
        query = {"name": filters[field]}
        documents = readable_documents(doctype, query, complete=True)
        if filters[field] not in documents:
            frappe.throw("所选客户或收银配置不存在或不属于该公司")
    return filters, start, end


def _exact(doctype, name, cache):
    if not name:
        frappe.throw("销售凭证缺少精确来源，请核对来源凭证")
    key = (doctype, name)
    if key not in cache:
        documents = readable_documents(doctype, {"name": name}, complete=True)
        if name not in documents:
            frappe.throw("销售凭证关联的来源不存在，请核对来源凭证")
        cache[key] = documents[name]
    return cache[key]


def _optional(doctype, name, cache):
    """Do not expose linked names or statuses without a full readable document."""
    if not name or not frappe.has_permission(doctype, "read"):
        return None
    key = ("optional", doctype, name)
    if key not in cache:
        cache[key] = readable_documents(doctype, {"name": name}).get(name)
    return cache[key]


def _stock_quantity(row):
    factor = decimal_number(row.get("conversion_factor"))
    if factor <= 0 or row.get("qty") is None or row.get("stock_qty") is None:
        frappe.throw("销售来源缺少有效的交易数量、库存数量或单位换算系数")
    qty, stock = decimal_number(row.qty), decimal_number(row.stock_qty)
    precision = row.precision("stock_qty") if callable(getattr(row, "precision", None)) else 6
    tolerance = Decimal(10) ** -int(precision if precision is not None else 6)
    if abs(stock - qty * factor) > tolerance:
        frappe.throw("销售来源库存数量与交易数量和单位换算系数不一致")
    return stock


def _same_party_currency(doc, source, same_customer=True):
    if (source.docstatus != 1 or source.company != doc.company
            or (same_customer and source.customer != doc.customer) or source.currency != doc.currency):
        frappe.throw("销售来源的提交状态、公司、客户或币种不一致")


def _return_source(doc, cache):
    field = "pos_invoice_item" if doc.doctype == POS else "sales_invoice_item"
    if not cint(doc.is_return):
        return None
    if not doc.get("return_against"):
        if any(row.get(field) for row in doc.items):
            frappe.throw("销售退货有来源明细却未关联原销售凭证")
        return None  # Native standalone credit notes remain distinct transactions.
    source = _exact(doc.doctype, doc.return_against, cache)
    _same_party_currency(doc, source)
    if cint(source.is_return) or decimal_number(source.conversion_rate) != decimal_number(doc.conversion_rate):
        frappe.throw("销售退货的原凭证方向或汇率不一致")
    items = {row.name: row for row in source.items}
    for row in doc.items:
        original = items.get(row.get(field))
        if (not original or row.item_code != original.item_code or row.stock_uom != original.stock_uom
                or _stock_quantity(row) > 0 or _stock_quantity(original) < 0):
            frappe.throw("销售退货缺少一致的原销售明细，请核对精确来源")
    return source


def _mirror_sources(invoice, cache):
    """Only exact submitted POS back references prove a duplicated invoice.

    A copied is_consolidated flag alone never removes a real Sales Invoice.
    Queries discover candidates without returning hidden record identifiers.
    Full source permission is required only when these candidates are present.
    """
    names = frappe.get_all(POS, filters={"consolidated_invoice": invoice.name},
                           pluck="name", distinct=True, limit_page_length=0)
    if not names:
        # Independent SI returns use their precise SI return reference. Copied
        # POS metadata is not evidence that a new POS business return exists.
        if not cint(invoice.is_return) and any(
                row.get("pos_invoice") or row.get("pos_invoice_item") for row in invoice.items):
            frappe.throw("销售发票包含零售来源明细，但缺少一致的原零售回指")
        return None
    sources = readable_documents(POS, {"name": ["in", names]}, complete=True)
    if set(sources) != set(names):
        frappe.throw("无法完整读取销售合并来源，请核对来源读取权限", frappe.PermissionError)
    if any(source.customer != invoice.customer for source in sources.values()):
        _group_merge(invoice, sources)
    expected, seen = set(), set()
    for source in sources.values():
        _same_party_currency(invoice, source, same_customer=False)
        if (not cint(invoice.is_consolidated) or cint(invoice.is_return) != cint(source.is_return)
                or source.consolidated_invoice != invoice.name):
            frappe.throw("销售合并凭证与原零售凭证的标记、方向或回指不一致")
        _return_source(source, cache)
        cache[(POS, source.name)] = source
        expected.update((source.name, row.name) for row in source.items)
    for row in invoice.items:
        identity = (row.get("pos_invoice"), row.get("pos_invoice_item"))
        source = sources.get(identity[0])
        original = next((item for item in source.items if item.name == identity[1]), None) if source else None
        if (not original or identity in seen or row.item_code != original.item_code
                or row.stock_uom != original.stock_uom or row.uom != original.uom
                or decimal_number(row.conversion_factor) != decimal_number(original.conversion_factor)
                or _stock_quantity(row) != _stock_quantity(original)):
            frappe.throw("销售合并缺少一致的原零售明细，请核对精确来源")
        seen.add(identity)
    if seen != expected:
        frappe.throw("销售合并未完整覆盖原零售明细，请核对来源")
    return sources


def _group_merge(invoice, sources):
    """Customer Group merges require a readable native log, not a guessed party."""
    parents = frappe.get_all("POS Invoice Reference", filters={
        "parenttype": "POS Invoice Merge Log", "pos_invoice": ["in", list(sources)]},
        pluck="parent", distinct=True, limit_page_length=0)
    logs = readable_documents("POS Invoice Merge Log", {
        "name": ["in", parents], "docstatus": 1, "merge_invoices_based_on": "Customer Group"},
        complete=True) if parents else {}
    for log in logs.values():
        members = {row.pos_invoice for row in log.get("pos_invoices") or []}
        if (log.company == invoice.company and log.customer == invoice.customer
                and set(sources) <= members
                and (invoice.name in (log.get("consolidated_invoice"), log.get("consolidated_credit_note"))
                     or all(source.consolidated_invoice == invoice.name for source in sources.values()))):
            return
    frappe.throw("销售合并的客户不同，且缺少完整可核对的客户组合并来源")


def _query(filters, start, end):
    query = {"company": filters.company, "docstatus": 1, "posting_date": ["between", [start, end]]}
    for field in ("customer", "pos_profile", "owner"):
        if filters.get(field):
            query[field] = filters[field]
    if cint(filters.get("only_returns")):
        query["is_return"] = 1
    return query


def _row(doc, company_currency, cache):
    source = _return_source(doc, cache)
    currency = doc.get("currency")
    conversion = decimal_number(doc.get("conversion_rate"))
    if not currency or conversion <= 0:
        frappe.throw("销售凭证缺少有效币种或汇率")
    linked = doc if doc.doctype == SI else _optional(SI, doc.get("consolidated_invoice"), cache)
    if linked and linked.company != doc.company:
        frappe.throw("销售关联过账凭证的公司不一致")
    closing_name = doc.get("pos_closing_entry") if doc.doctype == SI else None
    closing = _optional("POS Closing Entry", closing_name, cache)
    if closing and closing.company != doc.company:
        frappe.throw("销售关联结班凭证的公司不一致")
    row = {
        "doc_type": doc.doctype, "document": doc.name, "posting_date": doc.posting_date,
        "doc_type_label": "零售凭证" if doc.doctype == POS else "销售发票",
        "posting_time": doc.get("posting_time"), "entry_type": "退货" if cint(doc.is_return) else "销售",
        "status": doc.get("status"), "customer": doc.customer, "owner": doc.owner,
        "status_label": STATUS_LABELS.get(doc.get("status"), doc.get("status")),
        "pos_profile": doc.get("pos_profile"),
        "source_doctype": source.doctype if source else None, "source_document": source.name if source else None,
        "source_doctype_label": ("零售凭证" if source.doctype == POS else "销售发票") if source else None,
        "posted_invoice": linked.name if linked and linked.docstatus == 1 else None,
        "closing_entry": closing.name if closing else None,
        "is_consolidated": cint(doc.get("is_consolidated")),
        "has_posting_reference": int(doc.doctype == SI or bool(doc.get("consolidated_invoice"))),
        "currency": currency, "company_currency": company_currency, "conversion_rate": conversion,
    }
    for field in ("net_total", "base_net_total", "grand_total", "base_grand_total", "paid_amount",
                  "base_paid_amount", "change_amount", "base_change_amount", "write_off_amount",
                  "base_write_off_amount"):
        row[field] = decimal_number(doc.get(field))
    payments = doc.get("payments") or []
    row["gross_payment_amount"] = sum((decimal_number(payment.get("amount")) for payment in payments), Decimal(0))
    row["base_gross_payment_amount"] = sum((decimal_number(payment.get("base_amount")) for payment in payments), Decimal(0))
    row["net_tender"] = row["gross_payment_amount"] - row["change_amount"]
    row["base_net_tender"] = row["base_gross_payment_amount"] - row["base_change_amount"]
    row["base_loyalty_amount"] = decimal_number(doc.get("loyalty_amount"))
    return row


def _columns():
    return [
        column("doc_type_label", "来源类型"), column("document", "销售凭证", "Dynamic Link", "doc_type", 190),
        column("posting_date", "凭证记账日期", "Date"), column("posting_time", "凭证记账时间", "Time"),
        column("entry_type", "方向"), column("status_label", "本单状态"),
        column("customer", "客户", "Link", "Customer"), column("pos_profile", "本单收银配置名称"),
        column("owner", "创建人"), column("source_doctype_label", "原销售类型"),
        column("source_document", "原销售凭证", "Dynamic Link", "source_doctype", 190),
        column("posted_invoice", "已提交销售发票", "Link", SI, 190),
        column("closing_entry", "结班凭证", "Link", "POS Closing Entry", 190),
        column("is_consolidated", "合并标记", "Check"), column("has_posting_reference", "本单有关联过账记录", "Check"),
        column("currency", "原币", "Link", "Currency"), column("company_currency", "公司本币", "Link", "Currency"),
        column("conversion_rate", "凭证汇率", "Float"),
        *[column(field, label, "Currency", "company_currency" if field.startswith("base_") else "currency", 150)
          for field, label in (
              ("net_total", "原币未税净额"), ("base_net_total", "本币未税净额"),
              ("grand_total", "原币价税合计"), ("base_grand_total", "本币价税合计"),
              ("paid_amount", "原币凭证已付字段"), ("base_paid_amount", "本币凭证已付字段"),
              ("gross_payment_amount", "原币支付明细合计"), ("base_gross_payment_amount", "本币支付明细合计"),
              ("change_amount", "原币找零"), ("base_change_amount", "本币找零"),
              ("net_tender", "原币凭证收款净额"), ("base_net_tender", "本币凭证收款净额"),
              ("write_off_amount", "原币核销金额"), ("base_write_off_amount", "本币核销金额"),
              ("base_loyalty_amount", "本币积分抵扣字段"),
          )],
    ]


def sales_transaction_ledger(filters=None):
    """Original business transactions: exact POS mirrors are counted at POS only."""
    filters, start, end = _scope(filters)
    if filters.source_type == POS and not frappe.has_permission(POS, "read"):
        frappe.throw("没有读取原始零售凭证的权限", frappe.PermissionError)
    query, cache = _query(filters, start, end), {}
    company_currency = frappe.db.get_value("Company", filters.company, "default_currency")
    rows = []
    if filters.source_type != POS:
        for invoice in readable_documents(SI, query).values():
            if not _mirror_sources(invoice, cache):
                rows.append(_row(invoice, company_currency, cache))
    if filters.source_type != SI and frappe.has_permission(POS, "read"):
        rows.extend(_row(invoice, company_currency, cache)
                    for invoice in readable_documents(POS, query).values())
    rows.sort(key=lambda row: (str(row["posting_date"]), str(row["posting_time"]), row["doc_type"], row["document"]))
    return _columns(), rows


def posted_sales_ledger(filters=None):
    """All readable submitted Sales Invoices, including consolidated invoices."""
    filters, start, end = _scope(filters)
    if filters.source_type == POS:
        frappe.throw("已过账销售台账仅查询已提交销售发票，原始零售凭证请查询营业交易台账")
    company_currency = frappe.db.get_value("Company", filters.company, "default_currency")
    cache = {}
    rows = [_row(invoice, company_currency, cache)
            for invoice in readable_documents(SI, _query(filters, start, end)).values()]
    rows.sort(key=lambda row: (str(row["posting_date"]), str(row["posting_time"]), row["document"]))
    return _columns(), rows
