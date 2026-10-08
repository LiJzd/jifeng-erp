"""Permission-checked reconciliation view over native subcontracting documents.

The report never creates settlement documents. Supplier classification comes
only from an explicit Jifeng Partner Profile; financial rows are restricted to
Purchase Invoices whose rows link to the exact subcontracting Purchase Orders.
"""

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import getdate


PROFILE = "Jifeng Partner Profile"
KIND_LABELS = {"plating": "电镀厂", "outsourcing": "外发工厂"}
IDENTITY = {"name", "parent", "parenttype", "parentfield", "idx"}


def _deny():
    frappe.throw("无法完整读取所选委外来源及财务账簿，请核对公司、供应商和字段权限",
                 frappe.PermissionError)


def _fields(doctype, names, parenttype=None):
    if frappe.session.user == "Administrator":
        return
    meta = frappe.get_meta(doctype)
    permitted = set(get_permitted_fields(doctype, parenttype=parenttype,
                                          permission_type="read", ignore_virtual=True))
    levels = set(meta.get_permlevel_access(permission_type="mask", parenttype=parenttype,
                                            user=frappe.session.user))
    if not set(names).issubset(permitted):
        _deny()
    for name in names:
        field = meta.get_field(name)
        if not field:
            _deny()
        if field.get("mask") and (field.permlevel or 0) not in levels:
            _deny()


def _valid_name(value):
    return isinstance(value, str) and value and value == value.strip() and len(value) <= 140


def _date(value):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        frappe.throw("请选择有效的开始和结束日期")
    try:
        result = getdate(value)
    except (TypeError, ValueError):
        frappe.throw("日期必须为有效的 YYYY-MM-DD 日期")
    if result.isoformat() != value:
        frappe.throw("日期必须为明确的 YYYY-MM-DD 日期")
    return result


def _scope(company, supplier, from_date, to_date, business_kind):
    if not frappe.has_permission("Subcontracting Order", "read") or not frappe.has_permission("Subcontracting Receipt", "read"):
        _deny()
    if not _valid_name(company) or not _valid_name(supplier):
        frappe.throw("请选择单一公司和供应商")
    if business_kind not in ("all", "plating", "outsourcing"):
        frappe.throw("请选择有效的业务类型")
    start, end = _date(from_date), _date(to_date)
    if start > end:
        frappe.throw("开始日期不能晚于结束日期")
    if not frappe.get_list("Company", filters={"name": company}, pluck="name", limit_page_length=1):
        _deny()
    if not frappe.get_list("Supplier", filters={"name": supplier}, pluck="name", limit_page_length=1):
        _deny()
    return start, end


def _partner_kind(company, supplier):
    required = {"name", "company", "supplier", "archive_kind"}
    _fields(PROFILE, required)
    filters = {"company": company, "supplier": supplier}
    visible = frappe.get_list(PROFILE, filters=filters, fields=sorted(required), limit_page_length=0)
    raw_names = set(frappe.get_all(PROFILE, filters=filters, pluck="name", limit_page_length=0))
    if raw_names != {row.name for row in visible}:
        _deny()
    kinds = set()
    for row in visible:
        doc = frappe.get_doc(PROFILE, row.name)
        if not doc.has_permission("read") or doc.company != company or doc.supplier != supplier:
            _deny()
        _fields(PROFILE, required)
        if doc.archive_kind not in ("电镀厂", "外发工厂"):
            _deny()
        kinds.add(doc.archive_kind)
    if len(kinds) > 1:
        frappe.throw("该供应商在当前公司存在多个工厂分类，请先核对工厂档案")
    return next(iter(kinds), "未分类")


def _kind_matches(kind, business_kind):
    expected = KIND_LABELS.get(business_kind)
    return business_kind == "all" or kind == expected


def _visible_docs(doctype, filters, required):
    _fields(doctype, set(required) | {"name"})
    names = set(frappe.get_list(doctype, filters=filters, pluck="name", limit_page_length=0))
    raw_names = set(frappe.get_all(doctype, filters=filters, pluck="name", limit_page_length=0))
    if names != raw_names:
        _deny()
    result = {}
    for name in names:
        doc = frappe.get_doc(doctype, name)
        if not doc.has_permission("read"):
            _deny()
        _fields(doctype, set(required) | {"name"})
        result[name] = doc
    return result


def _child_rows(doctype, parenttype, parentfield, filters, required):
    _fields(doctype, set(required) | IDENTITY, parenttype=parenttype)
    filters = {**filters, "parenttype": parenttype, "parentfield": parentfield}
    fields = sorted(set(required) | IDENTITY)
    visible = frappe.get_list(doctype, filters=filters, fields=fields, order_by="parent asc, idx asc",
                              limit_page_length=0)
    raw_names = set(frappe.get_all(doctype, filters=filters, pluck="name", limit_page_length=0))
    if raw_names != {row.name for row in visible}:
        _deny()
    return visible


def _source_order_data(company, supplier, source_names):
    if not source_names:
        return {}, {}
    orders = _visible_docs("Subcontracting Order", {
        "name": ["in", sorted(source_names)],
        "company": company, "supplier": supplier, "docstatus": 1,
    }, {"company", "supplier", "purchase_order", "transaction_date", "docstatus"})
    po_names = sorted({doc.purchase_order for doc in orders.values() if doc.purchase_order})
    purchase_orders = _visible_docs("Purchase Order", {
        "name": ["in", po_names], "company": company, "supplier": supplier, "docstatus": 1,
    }, {"company", "supplier", "docstatus", "is_subcontracted", "transaction_date"}) if po_names else {}
    for order in orders.values():
        if order.purchase_order:
            po = purchase_orders.get(order.purchase_order)
            if not po or not po.is_subcontracted or po.supplier != supplier or po.company != company:
                frappe.throw("委外订单的采购来源无法核对，不能归类财务金额")
    return orders, purchase_orders


def _purchase_invoice_closure(company, supplier, start, end, purchase_orders):
    po_names = set(purchase_orders)
    if not po_names:
        return {}, set()
    rows = _child_rows("Purchase Invoice Item", "Purchase Invoice", "items", {
        "purchase_order": ["in", sorted(po_names)],
    }, {"purchase_order", "parent"})
    invoice_names = {row.parent for row in rows if row.purchase_order in po_names}
    if not invoice_names:
        return {}, set()
    invoices = _visible_docs("Purchase Invoice", {
        "name": ["in", sorted(invoice_names)], "company": company, "supplier": supplier,
        "docstatus": 1, "posting_date": ["<=", end.isoformat()],
    }, {"company", "supplier", "docstatus", "posting_date", "is_return", "return_against", "currency"})
    # A submitted supplier credit note is included only when its native
    # return_against points to an in-scope, PO-linked invoice.
    returns = _visible_docs("Purchase Invoice", {
        "company": company, "supplier": supplier, "docstatus": 1, "is_return": 1,
        "return_against": ["in", sorted(invoices)],
        "posting_date": ["<=", end.isoformat()],
    }, {"company", "supplier", "docstatus", "posting_date", "is_return", "return_against", "currency"}) if invoices else {}
    return {**invoices, **returns}, set(invoices) | set(returns)


def _select_payables(report_rows, invoice_names):
    rows = [row for row in report_rows if row.get("voucher_type") == "Purchase Invoice"
            and row.get("voucher_no") in invoice_names]
    if {row.get("voucher_no") for row in rows} != set(invoice_names):
        frappe.throw("委外发票与原生应付账簿不完整对应，不能显示部分余额")
    return rows


def _payment_scope(references, invoice_names, purchase_order_names):
    if not references:
        return "unlinked"
    scoped = all((row.reference_doctype == "Purchase Invoice" and row.reference_name in invoice_names)
                 or (row.reference_doctype == "Purchase Order" and row.reference_name in purchase_order_names)
                 for row in references)
    if not scoped:
        return "mixed"
    if all(row.reference_doctype == "Purchase Invoice" and row.reference_name in invoice_names for row in references):
        return "invoice"
    if all(row.reference_doctype == "Purchase Order" and row.reference_name in purchase_order_names for row in references):
        return "purchase_order"
    return "scoped_mixed"


def _payable_rows(company, supplier, end, invoice_docs):
    if not invoice_docs:
        return []
    from jifeng_erp.supplier_reconciliation import query_payables

    result = query_payables(company, supplier, end.isoformat())
    if result.get("company") != company or result.get("supplier") != supplier or result.get("report_date") != end.isoformat():
        frappe.throw("原生供应商应付报告范围发生变化，请重新查询")
    return _select_payables(result.get("rows", []), set(invoice_docs))


def _payments(company, supplier, start, end, purchase_orders, invoice_docs):
    invoice_names = set(invoice_docs)
    po_names = set(purchase_orders)
    if not invoice_names and not po_names:
        return []
    ref_filters = {"parenttype": "Payment Entry", "reference_doctype": ["in", ["Purchase Invoice", "Purchase Order"]],
                   "reference_name": ["in", sorted(invoice_names | po_names)]}
    refs = _child_rows("Payment Entry Reference", "Payment Entry", "references", ref_filters,
                       {"parent", "reference_doctype", "reference_name"})
    candidates = {row.parent for row in refs}
    if not candidates:
        return []
    all_refs = _child_rows("Payment Entry Reference", "Payment Entry", "references",
                           {"parent": ["in", sorted(candidates)]},
                           {"parent", "reference_doctype", "reference_name"})
    refs_by_parent = {}
    for row in all_refs:
        refs_by_parent.setdefault(row.parent, []).append(row)
    docs = _visible_docs("Payment Entry", {
        "name": ["in", sorted(candidates)], "company": company, "party_type": "Supplier",
        "party": supplier, "payment_type": "Pay", "docstatus": 1,
        "posting_date": ["between", [start.isoformat(), end.isoformat()]],
    }, {"company", "party_type", "party", "payment_type", "docstatus", "posting_date", "paid_amount",
        "paid_from_account_currency", "paid_from", "paid_to"})
    payments = []
    for name, doc in docs.items():
        references = refs_by_parent.get(name, [])
        if not references:
            frappe.throw("已提交付款缺少可核对的应付来源明细")
        scope = _payment_scope(references, invoice_names, po_names)
        if scope in ("unlinked", "mixed"):
            payments.append({"voucher_no": name, "posting_date": str(doc.posting_date),
                "currency": None, "paid_amount": None,
                "payment_scope": "付款来源缺失，无法核对" if scope == "unlinked" else "混合来源，无法归入本委外范围"})
            continue
        if not doc.paid_from_account_currency or doc.paid_amount in (None, ""):
            frappe.throw("付款来源币种或金额缺失，不能显示实付")
        payment_scope = ("发票已核销付款" if scope == "invoice" else
                         "委外订单预付款（尚未核销到发票）" if scope == "purchase_order" else "委外范围混合付款")
        payments.append({"voucher_no": name, "posting_date": str(doc.posting_date),
            "currency": doc.paid_from_account_currency, "paid_amount": str(doc.paid_amount),
            "payment_scope": payment_scope})
    return payments


def _cancelled_history(company, supplier, start, end, orders, purchase_orders):
    """Expose cancelled vouchers as links only; they never enter active totals."""
    date_range = [start.isoformat(), end.isoformat()]
    history = []
    cancelled_orders = _visible_docs("Subcontracting Order", {
        "company": company, "supplier": supplier, "docstatus": 2,
        "transaction_date": ["between", date_range],
    }, {"company", "supplier", "docstatus", "transaction_date", "purchase_order"})
    cancelled_pos = _visible_docs("Purchase Order", {
        "company": company, "supplier": supplier, "docstatus": 2, "is_subcontracted": 1,
        "transaction_date": ["between", date_range],
    }, {"company", "supplier", "docstatus", "transaction_date", "is_subcontracted"})
    cancelled_receipts = _visible_docs("Subcontracting Receipt", {
        "company": company, "supplier": supplier, "docstatus": 2,
        "posting_date": ["between", date_range],
    }, {"company", "supplier", "docstatus", "posting_date", "is_return"})
    for doctype, docs, date_field in (
        ("Subcontracting Order", cancelled_orders, "transaction_date"),
        ("Purchase Order", cancelled_pos, "transaction_date"),
        ("Subcontracting Receipt", cancelled_receipts, "posting_date"),
    ):
        history.extend({"doctype": doctype, "voucher_no": doc.name, "posting_date": str(doc.get(date_field) or ""),
                        "status": "已取消"} for doc in docs.values())

    order_names = set(orders) | set(cancelled_orders)
    if order_names:
        cancelled_entries = _visible_docs("Stock Entry", {
            "company": company, "docstatus": 2, "subcontracting_order": ["in", sorted(order_names)],
            "posting_date": ["between", date_range],
        }, {"company", "docstatus", "subcontracting_order", "posting_date", "purpose", "is_return"})
        history.extend({"doctype": "Stock Entry", "voucher_no": doc.name,
                        "posting_date": str(doc.posting_date or ""), "status": "已取消"}
                       for doc in cancelled_entries.values())

    po_names = set(purchase_orders) | set(cancelled_pos)
    invoice_names = set()
    if po_names:
        invoice_rows = _child_rows("Purchase Invoice Item", "Purchase Invoice", "items",
            {"purchase_order": ["in", sorted(po_names)]}, {"parent", "purchase_order"})
        invoice_names.update(row.parent for row in invoice_rows)
    if invoice_names:
        cancelled_invoices = _visible_docs("Purchase Invoice", {
            "company": company, "supplier": supplier, "docstatus": 2,
            "name": ["in", sorted(invoice_names)], "posting_date": ["<=", end.isoformat()],
        }, {"company", "supplier", "docstatus", "posting_date", "return_against", "is_return"})
        history.extend({"doctype": "Purchase Invoice", "voucher_no": doc.name,
                        "posting_date": str(doc.posting_date or ""), "status": "已取消"}
                       for doc in cancelled_invoices.values())
    reference_names = invoice_names | po_names
    if reference_names:
        refs = _child_rows("Payment Entry Reference", "Payment Entry", "references", {
            "reference_doctype": ["in", ["Purchase Invoice", "Purchase Order"]],
            "reference_name": ["in", sorted(reference_names)],
        }, {"parent", "reference_doctype", "reference_name"})
        payment_names = {row.parent for row in refs}
        if payment_names:
            cancelled_payments = _visible_docs("Payment Entry", {
                "company": company, "party_type": "Supplier", "party": supplier, "docstatus": 2,
                "name": ["in", sorted(payment_names)], "posting_date": ["<=", end.isoformat()],
            }, {"company", "party_type", "party", "docstatus", "posting_date", "payment_type"})
            history.extend({"doctype": "Payment Entry", "voucher_no": doc.name,
                            "posting_date": str(doc.posting_date or ""), "status": "已取消"}
                           for doc in cancelled_payments.values())
    return sorted(history, key=lambda row: (row["posting_date"], row["doctype"], row["voucher_no"]))


def _plain_rows(rows):
    return [{key: (str(value) if value is not None and key.endswith(("_qty", "_value_change", "_amount", "invoiced", "paid", "credit_note", "outstanding")) else value)
             for key, value in row.items()} for row in rows]


@frappe.whitelist(methods=["GET"])
def query_reconciliation(company, supplier, from_date, to_date, business_kind="all",
                         flow_order=None, subcontracting_order=None):
    """Return source-linked activity; no local settlement or payable arithmetic."""
    start, end = _scope(company, supplier, from_date, to_date, business_kind)
    kind = _partner_kind(company, supplier)
    if not _kind_matches(kind, business_kind):
        frappe.throw("所选工厂档案分类与当前对账类型不一致")
    from jifeng_erp.subcontracting_material import subcontracting_material_ledger
    from jifeng_erp.subcontracting import subcontracting_progress, subcontracting_receipt_ledger

    filters = {"company": company, "supplier": supplier, "from_date": start.isoformat(), "to_date": end.isoformat(),
               "flow_order": flow_order or None, "subcontracting_order": subcontracting_order or None}
    _, progress = subcontracting_progress(filters)
    _, receipts = subcontracting_receipt_ledger(filters)
    _, materials = subcontracting_material_ledger(filters)
    source_names = ({row["subcontracting_order"] for row in progress} |
                    {row["subcontracting_order"] for row in receipts} |
                    {row["subcontracting_order"] for row in materials})
    orders, purchase_orders = _source_order_data(company, supplier, source_names)
    invoice_docs, _ = _purchase_invoice_closure(company, supplier, start, end, purchase_orders)
    payable_rows = _payable_rows(company, supplier, end, invoice_docs)
    payments = _payments(company, supplier, start, end, purchase_orders, invoice_docs)
    company_doc = frappe.get_doc("Company", company)
    company_doc.check_permission("select")
    _fields("Company", {"name", "default_currency"})
    history = _cancelled_history(company, supplier, start, end, orders, purchase_orders)
    return {"company": company, "supplier": supplier, "supplier_kind": kind,
        "business_kind": business_kind, "from_date": start.isoformat(), "to_date": end.isoformat(),
        "flow_order": flow_order or None, "subcontracting_order": subcontracting_order or None,
        "company_currency": company_doc.default_currency,
        "progress": _plain_rows(progress), "receipts": _plain_rows(receipts),
        "materials": _plain_rows(materials),
        "payables": _plain_rows(payable_rows), "payments": payments, "history": history,
        "payment_basis": "所选日期内已提交Payment Entry且引用本范围采购发票/委外采购单；混合引用的付款不计入金额。金额按付款账户币种逐笔展示",
        "payable_basis": "截至结束日期的原生Accounts Payable报告；仅列有委外采购单直接来源的发票及其贷项闭包。应付、已核销、贷项及未付余额均为公司本币；不以发票总额减余额推算实付",
        "settlement_action": "请打开原生Purchase Invoice/Payment Entry进行保存、审核或付款"}
