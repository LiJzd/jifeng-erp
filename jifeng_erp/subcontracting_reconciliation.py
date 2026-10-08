"""Permission-checked reconciliation view over native subcontracting documents.

The report never creates settlement documents. Supplier classification comes
only from an explicit Jifeng Partner Profile; financial rows are restricted to
Purchase Invoices whose rows link to the exact subcontracting Purchase Orders.
"""

from decimal import Decimal, InvalidOperation

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import getdate


PROFILE = "Jifeng Partner Profile"
KIND_LABELS = {"plating": "电镀厂", "outsourcing": "外发工厂"}
IDENTITY = {"name", "parent", "parenttype", "parentfield", "idx"}
SYSTEM_FIELDS = IDENTITY | {"docstatus", "modified", "owner", "creation", "modified_by"}
INVOICE_FIELDS = {"company", "supplier", "docstatus", "posting_date", "is_return", "return_against", "currency",
                  "credit_to", "grand_total", "rounded_total", "outstanding_amount"}


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
            if name in SYSTEM_FIELDS:
                continue
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
    if len(visible) > 1:
        frappe.throw("该供应商在当前公司存在多个工厂档案，请先核对唯一档案")
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
    table = frappe.get_meta(parenttype).get_field(parentfield)
    if not table or table.fieldtype != "Table" or table.options != doctype:
        frappe.throw("对账来源明细表定义不一致，请核对原生单据")
    if frappe.session.user != "Administrator":
        meta = frappe.get_meta(parenttype)
        levels = set(meta.get_permlevel_access(permission_type="read", user=frappe.session.user))
        masks = set(meta.get_permlevel_access(permission_type="mask", user=frappe.session.user))
        if ((table.permlevel or 0) not in levels
                or table.get("mask") and (table.permlevel or 0) not in masks):
            _deny()
    _fields(doctype, set(required) | IDENTITY, parenttype=parenttype)
    filters = {**filters, "parenttype": parenttype, "parentfield": parentfield}
    fields = sorted(set(required) | IDENTITY)
    visible = frappe.get_list(doctype, filters=filters, fields=fields, order_by="parent asc, idx asc",
                              parent_doctype=parenttype, limit_page_length=0)
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
    if set(orders) != set(source_names) or any(not doc.purchase_order for doc in orders.values()):
        frappe.throw("委外订单缺少完整的采购来源，不能归类财务金额")
    po_names = sorted({doc.purchase_order for doc in orders.values() if doc.purchase_order})
    purchase_orders = _visible_docs("Purchase Order", {
        "name": ["in", po_names], "company": company, "supplier": supplier, "docstatus": 1,
    }, {"company", "supplier", "docstatus", "is_subcontracted", "transaction_date"}) if po_names else {}
    for order in orders.values():
        if order.purchase_order:
            po = purchase_orders.get(order.purchase_order)
            if not po or po.is_subcontracted != 1 or po.supplier != supplier or po.company != company:
                frappe.throw("委外订单的采购来源无法核对，不能归类财务金额")
    return orders, purchase_orders


def _financial_source_names(company, supplier, end, flow_order=None, subcontracting_order=None):
    # The payable cutoff and this period's payments can concern older orders
    # with no receipt/material movement in the current date range.
    filters = {"company": company, "supplier": supplier, "docstatus": 1,
               "transaction_date": ["<=", end.isoformat()]}
    if subcontracting_order:
        filters["name"] = subcontracting_order
    docs = _visible_docs("Subcontracting Order", filters, {"company", "supplier", "docstatus", "transaction_date"})
    if not flow_order or not docs:
        return set(docs)
    rows = _child_rows("Subcontracting Order Item", "Subcontracting Order", "items",
                      {"parent": ["in", sorted(docs)]}, {"jf_flow_order"})
    return {row.parent for row in rows if row.jf_flow_order == flow_order}


def _flow_purchase_orders(purchase_orders, flow_order):
    if not flow_order or not purchase_orders:
        return set(purchase_orders)
    rows = _child_rows("Purchase Order Item", "Purchase Order", "items",
                      {"parent": ["in", sorted(purchase_orders)]}, {"jf_flow_order"})
    items_by_parent = {}
    for row in rows:
        items_by_parent.setdefault(row.parent, []).append(row)
    # A native PI references the PO, so a Flow filter cannot allocate the
    # entire payable of a PO containing other flows or unlinked items.
    return {name for name in purchase_orders if items_by_parent.get(name)
            and all(row.jf_flow_order == flow_order for row in items_by_parent[name])}


def _purchase_invoice_closure(company, supplier, start, end, purchase_orders, exact_po_names=None):
    po_names = set(purchase_orders)
    exact_po_names = po_names if exact_po_names is None else set(exact_po_names)
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
    }, INVOICE_FIELDS)
    # A submitted supplier credit note is included only when its native
    # return_against points to an in-scope, PO-linked invoice.
    returns = _visible_docs("Purchase Invoice", {
        "company": company, "supplier": supplier, "docstatus": 1, "is_return": 1,
        "return_against": ["in", sorted(invoices)],
        "posting_date": ["<=", end.isoformat()],
    }, INVOICE_FIELDS) if invoices else {}
    docs = {**invoices, **returns}
    all_rows = _child_rows("Purchase Invoice Item", "Purchase Invoice", "items",
                          {"parent": ["in", sorted(docs)]}, {"purchase_order"}) if docs else []
    items_by_parent = {}
    for row in all_rows:
        items_by_parent.setdefault(row.parent, []).append(row)
    exact = {name for name, doc in invoices.items() if not (doc.is_return and doc.return_against)
             and items_by_parent.get(name)
             and all(row.purchase_order in exact_po_names for row in items_by_parent[name])}
    # Native return_against proves credit-note membership only when the whole
    # original invoice is in scope and its rows introduce no other PO.
    exact.update(name for name, doc in docs.items() if doc.is_return and doc.return_against in exact
                 and items_by_parent.get(name)
                 and all(not row.purchase_order or row.purchase_order in exact_po_names for row in items_by_parent[name]))
    return docs, exact


def _select_payables(report_rows, invoice_names):
    rows = [row for row in report_rows if row.get("voucher_type") == "Purchase Invoice"
            and row.get("voucher_no") in invoice_names]
    listed = {row.get("voucher_no") for row in rows}
    # Native Accounts Payable can omit fully cleared invoices. Omission does
    # not prove zero, cash payment or a credit; retain the source link only.
    return [*rows, *({"voucher_type": "Purchase Invoice", "voucher_no": name,
                      "source_scope": "原生应付报告未列示，请查原生发票及付款",
                      **{field: None for field in ("invoiced", "paid", "credit_note", "outstanding")}}
                    for name in sorted(set(invoice_names) - listed))]


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


def _payable_rows(company, supplier, end, invoice_docs, exact_invoice_names):
    if not invoice_docs:
        return []
    from jifeng_erp.supplier_reconciliation import query_payables

    result = query_payables(company, supplier, end.isoformat())
    if result.get("company") != company or result.get("supplier") != supplier or result.get("report_date") != end.isoformat():
        frappe.throw("原生供应商应付报告范围发生变化，请重新查询")
    rows = _select_payables(result.get("rows", []), set(invoice_docs))
    rows = [{**row, "posting_date": row.get("posting_date") or str(invoice_docs[row["voucher_no"]].posting_date),
             "source_scope": (row.get("source_scope") or "完整委外采购来源") if row["voucher_no"] in exact_invoice_names else
             "混合或缺失采购来源，无法归入本委外范围",
             **({} if row["voucher_no"] in exact_invoice_names else
                {field: None for field in ("invoiced", "paid", "credit_note", "outstanding")})}
            for row in rows]
    for row in rows:
        if row["voucher_no"] in exact_invoice_names and row["source_scope"].startswith("原生应付报告未列示"):
            current = _visible_docs("Purchase Invoice", {"name": row["voucher_no"], "company": company,
                "supplier": supplier, "docstatus": 1, "posting_date": ["<=", end.isoformat()]}, INVOICE_FIELDS)
            invoice = current.get(row["voucher_no"])
            if not invoice:
                frappe.throw("发票当前状态已变化，请重新查询委外对账")
            account = _account(invoice.credit_to, company)
            if account.account_type != "Payable":
                frappe.throw("采购发票的原生应付科目类型不一致，请核对原单据")
            # Native outstanding_amount is in the payable-account currency.
            # A foreign account/invoice mismatch cannot share grand_total's unit.
            if account.account_currency != invoice.currency:
                row["source_scope"] = "原生应付报告未列示，发票与应付账户币种不同，请查原生来源"
                continue
            row.update({"payable_account": account.name, "currency": invoice.currency,
                        "invoiced": str(_finite_amount(invoice.rounded_total or invoice.grand_total)),
                        "outstanding": str(_finite_amount(invoice.outstanding_amount)),
                        "source_scope": "发票原生余额（当前；AP截至日未列示）"})
    return rows


def _account(name, company):
    _fields("Account", {"name", "company", "account_currency", "account_type", "is_group"})
    account = frappe.get_doc("Account", name)
    if not account.has_permission("read") or account.company != company or account.is_group:
        _deny()
    return account


def _payments(company, supplier, start, end, purchase_orders, invoice_docs, exact_invoice_names, exact_po_names=None):
    invoice_names = set(invoice_docs)
    po_names = set(purchase_orders)
    if not invoice_names and not po_names:
        return []
    ref_filters = {"parenttype": "Payment Entry", "reference_doctype": ["in", ["Purchase Invoice", "Purchase Order"]],
                   "reference_name": ["in", sorted(invoice_names | po_names)]}
    refs = _child_rows("Payment Entry Reference", "Payment Entry", "references", ref_filters,
                       {"parent", "reference_doctype", "reference_name", "allocated_amount"})
    candidates = {row.parent for row in refs}
    if not candidates:
        return []
    all_refs = _child_rows("Payment Entry Reference", "Payment Entry", "references",
                           {"parent": ["in", sorted(candidates)]},
                           {"parent", "reference_doctype", "reference_name", "allocated_amount"})
    refs_by_parent = {}
    for row in all_refs:
        refs_by_parent.setdefault(row.parent, []).append(row)
    docs = _visible_docs("Payment Entry", {
        "name": ["in", sorted(candidates)], "company": company, "party_type": "Supplier",
        "party": supplier, "payment_type": "Pay", "docstatus": 1,
        "posting_date": ["between", [start.isoformat(), end.isoformat()]],
    }, {"company", "party_type", "party", "payment_type", "docstatus", "posting_date", "paid_amount",
        "paid_from_account_currency", "paid_from", "paid_to", "unallocated_amount", "total_allocated_amount"})
    payments = []
    for name, doc in docs.items():
        references = refs_by_parent.get(name, [])
        if not references:
            frappe.throw("已提交付款缺少可核对的应付来源明细")
        scope = _payment_scope(references, exact_invoice_names, po_names if exact_po_names is None else exact_po_names)
        if (not all(_finite_amount(row.allocated_amount) > 0 for row in references)
                or _finite_amount(doc.total_allocated_amount) <= 0
                or _finite_amount(doc.unallocated_amount) != 0):
            scope = "unlinked"
        if scope in ("unlinked", "mixed"):
            payments.append({"voucher_no": name, "posting_date": str(doc.posting_date),
                "currency": None, "paid_amount": None,
                "payment_scope": "付款未完整核销到有效来源，无法核对实付" if scope == "unlinked" else "混合来源，无法归入本委外范围"})
            continue
        if not doc.paid_from_account_currency or doc.paid_amount in (None, ""):
            frappe.throw("付款来源币种或金额缺失，不能显示实付")
        if _finite_amount(doc.paid_amount) <= 0:
            frappe.throw("付款来源金额必须是大于零的有限数字")
        source_account = _account(doc.paid_from, company)
        party_account = _account(doc.paid_to, company)
        if source_account.account_currency != doc.paid_from_account_currency or party_account.account_type != "Payable":
            frappe.throw("付款来源账户币种或应付科目不一致，请核对原生单据")
        if source_account.account_type not in ("Bank", "Cash"):
            payments.append({"voucher_no": name, "posting_date": str(doc.posting_date),
                "currency": None, "paid_amount": None, "payment_scope": "来源并非银行或现金账户，无法核对实付"})
            continue
        payment_scope = ("发票已核销付款" if scope == "invoice" else
                         "委外订单预付款（尚未核销到发票）" if scope == "purchase_order" else "委外范围混合付款")
        payments.append({"voucher_no": name, "posting_date": str(doc.posting_date),
            "currency": doc.paid_from_account_currency, "paid_amount": str(doc.paid_amount),
            "payment_scope": payment_scope})
    return payments


def _finite_amount(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw("原生来源金额缺失或无效，请核对原单据")
    if not amount.is_finite():
        frappe.throw("原生来源金额缺失或无效，请核对原单据")
    return amount


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
    source_names |= _financial_source_names(company, supplier, end, flow_order, subcontracting_order)
    orders, purchase_orders = _source_order_data(company, supplier, source_names)
    exact_po_names = _flow_purchase_orders(purchase_orders, flow_order)
    invoice_docs, exact_invoice_names = _purchase_invoice_closure(company, supplier, start, end, purchase_orders, exact_po_names)
    payable_rows = _payable_rows(company, supplier, end, invoice_docs, exact_invoice_names)
    payments = _payments(company, supplier, start, end, purchase_orders, invoice_docs, exact_invoice_names, exact_po_names)
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
        "payable_basis": "原生Accounts Payable按结束日期及公司本币展示；未列示的明确委外发票按发票币种显示原生当前金额与余额，并逐行注明依据。混合、缺失来源或无法对应币种的发票保留链接、金额留空；不以发票总额减余额推算现金实付",
        "settlement_action": "请打开原生Purchase Invoice/Payment Entry进行保存、审核或付款"}
