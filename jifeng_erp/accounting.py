"""Expense provenance and native, single-currency Supplier payable controls."""

from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation

import frappe

SOURCE = "Jifeng Expense Voucher"
CREDIT = "赊账"
METHODS = ("现金", "银行转账", "其他", CREDIT)
SOURCE_FIELDS = ("name", "company", "docstatus", "posting_date", "payment_method", "expense_account",
                 "payment_account", "supplier", "payable_account", "cost_center", "amount", "journal_entry")
ACCOUNT_FIELDS = ("name", "company", "is_group", "disabled", "account_currency", "root_type", "account_type")
JOURNAL_FIELDS = ("name", "company", "docstatus", "posting_date", "voucher_type", "jf_expense_voucher", "multi_currency")
JOURNAL_ROW_FIELDS = ("name", "account", "party_type", "party", "cost_center", "debit_in_account_currency",
                      "credit_in_account_currency", "reference_type", "reference_name")
_journal_context = ContextVar("jifeng_expense_journal_context", default=None)


def require_fields(doctype, fields, parenttype=None):
    from frappe.model import get_permitted_fields

    permitted = set(get_permitted_fields(doctype, parenttype=parenttype, permission_type="read"))
    if not set(fields).issubset(permitted):
        frappe.throw("没有读取费用及会计来源必要字段的权限", frappe.PermissionError)


def require_table(doctype, fieldname):
    # Table is not a physical column and is excluded by get_permitted_fields.
    meta = frappe.get_meta(doctype)
    field = meta.get_field(fieldname)
    levels = meta.get_permlevel_access(permission_type="read", user=frappe.session.user)
    if not field or field.fieldtype != "Table" or (field.permlevel or 0) not in levels:
        frappe.throw("没有读取会计明细的权限", frappe.PermissionError)


def _reference(doctype, name, fields, *, lock=False, select=True):
    if not name:
        frappe.throw("请明确选择公司、供应商、会计科目和成本中心")
    document = frappe.get_doc(doctype, name, for_update=lock)
    document.check_permission("read")
    if select:
        document.check_permission("select")
    require_fields(doctype, fields)
    return document


def positive_amount(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw("费用金额必须是有效数字")
    if not amount.is_finite() or amount <= 0:
        frappe.throw("费用金额必须大于零")
    return amount


def _expense_references(doc, *, active=True, lock=False):
    require_fields(SOURCE, SOURCE_FIELDS)
    company = _reference("Company", doc.company, ("name", "default_currency"))
    currency = company.default_currency
    if not currency:
        frappe.throw("请先配置公司的默认币种")
    if doc.payment_method not in METHODS:
        frappe.throw("请选择明确的费用方式")
    credit = doc.payment_method == CREDIT
    offset = doc.get("payable_account") if credit else doc.get("payment_account")
    if credit and (not doc.get("supplier") or not offset):
        frappe.throw("赊账费用必须明确选择供应商和应付科目")
    if not credit and not offset:
        frappe.throw("现金、银行转账及其他方式必须选择付款科目")
    if doc.expense_account == offset:
        frappe.throw("费用科目与付款或应付科目不能相同")
    expense = _reference("Account", doc.expense_account, ACCOUNT_FIELDS, lock=lock)
    payment = _reference("Account", offset, ACCOUNT_FIELDS, lock=lock)
    cost_center = _reference("Cost Center", doc.cost_center, ("name", "company", "is_group"))
    for account in (expense, payment):
        if account.company != doc.company or account.is_group or (active and account.disabled):
            frappe.throw("请选择当前公司启用的明细会计科目")
        if account.account_currency and account.account_currency != currency:
            frappe.throw("费用登记暂只支持公司默认币种的会计科目")
    if cost_center.company != doc.company or cost_center.is_group:
        frappe.throw("请选择当前公司的明细成本中心")
    if expense.root_type != "Expense":
        frappe.throw("费用科目必须属于费用类")
    if credit:
        supplier = _reference("Supplier", doc.supplier, ("name", "disabled"), lock=lock)
        if active and supplier.disabled:
            frappe.throw("请选择启用的供应商")
        if payment.root_type != "Liability" or payment.account_type != "Payable":
            frappe.throw("赊账费用必须选择负债类应付科目")
    else:
        if payment.root_type != "Asset" or payment.account_type not in ("Cash", "Bank"):
            frappe.throw("付款科目必须是现金或银行资产科目")
        if doc.payment_method == "现金" and payment.account_type != "Cash":
            frappe.throw("现金付款只能选择现金科目")
        if doc.payment_method == "银行转账" and payment.account_type != "Bank":
            frappe.throw("银行转账只能选择银行科目")
    return currency


def validate_expense_voucher(doc):
    _expense_references(doc)
    return positive_amount(doc.amount)


@contextmanager
def expense_journal_action(journal, source):
    """Authorize this exact server object only for the enclosed operation."""
    token = _journal_context.set((journal, source))
    try:
        yield
    finally:
        _journal_context.reset(token)


def _managed_source(doc):
    source = doc.get("jf_expense_voucher")
    before = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
    return source or (before.get("jf_expense_voucher") if before else None)


def _authorized(doc, source):
    context = _journal_context.get()
    return bool(context and context[0] is doc and context[1] == source and doc.get("jf_expense_voucher") == source)


def validate_expense_journal_entry(doc, method=None):
    source = _managed_source(doc)
    if source and not _authorized(doc, source):
        frappe.throw("集沣费用登记的总账凭证只能由费用登记创建和修改")


def validate_expense_journal_entry_cancel(doc, method=None):
    source = _managed_source(doc)
    if source and not _authorized(doc, source):
        frappe.throw("请从集沣费用登记取消关联的总账凭证")


def expense_journal(doc, *, lock=False):
    if not doc.get("journal_entry"):
        frappe.throw("费用登记缺少关联总账凭证，请先核对")
    currency = _expense_references(doc, active=False)
    journal = _reference("Journal Entry", doc.journal_entry, JOURNAL_FIELDS, lock=lock, select=False)
    require_table("Journal Entry", "accounts")
    require_fields("Journal Entry Account", JOURNAL_ROW_FIELDS, parenttype="Journal Entry")
    if journal.get("jf_expense_voucher") != doc.name or journal.company != doc.company:
        frappe.throw("关联总账凭证的来源费用登记不一致")
    if journal.docstatus != 1 or journal.get("multi_currency"):
        frappe.throw("关联总账凭证不是已提交的本币凭证，请先核对")
    from frappe.utils import getdate

    if journal.voucher_type != "Journal Entry" or getdate(journal.posting_date) != getdate(doc.posting_date):
        frappe.throw("关联总账凭证的类型和费用日期不一致")
    if len(journal.accounts) != 2:
        frappe.throw("关联总账凭证的费用和应付明细不一致")
    debit, credit = journal.accounts
    amount = positive_amount(doc.amount)
    offset = doc.get("payable_account") if doc.payment_method == CREDIT else doc.get("payment_account")
    if (debit.get("account") != doc.expense_account or debit.get("cost_center") != doc.cost_center
            or Decimal(str(debit.get("debit_in_account_currency") or 0)) != amount
            or Decimal(str(debit.get("credit_in_account_currency") or 0)) != 0
            or credit.get("account") != offset
            or Decimal(str(credit.get("credit_in_account_currency") or 0)) != amount
            or Decimal(str(credit.get("debit_in_account_currency") or 0)) != 0
            or any(row.get("reference_type") or row.get("reference_name") for row in journal.accounts)
            or debit.get("party_type") or debit.get("party")):
        frappe.throw("关联总账凭证的费用和应付明细不一致")
    party = ("Supplier", doc.get("supplier")) if doc.payment_method == CREDIT else (None, None)
    if (credit.get("party_type") or None, credit.get("party") or None) != party:
        frappe.throw("关联总账凭证的供应商应付来源不一致")
    return journal, currency


def credit_reference_details(doc, *, lock=False):
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_reference_details

    if doc.docstatus != 1 or doc.payment_method != CREDIT:
        frappe.throw("请选择已提交的赊账费用")
    journal, currency = expense_journal(doc, lock=lock)
    details = get_reference_details("Journal Entry", journal.name, currency, "Supplier", doc.supplier)
    if positive_amount(details.get("total_amount")) != positive_amount(doc.amount):
        frappe.throw("实际应付总额与来源费用不一致，请先核对原生账簿")
    return journal, currency, details


def _locked_credit_dependencies(doctype, filters):
    """Current reads even when this transaction already has an MVCC snapshot."""
    table = frappe.qb.DocType(doctype)
    query = frappe.qb.from_(table).select(table.star)
    for field, value in filters.items():
        query = query.where(table[field] == value)
    # Internal complete reference closure only; these rows are never DTO data.
    return query.for_update().run(as_dict=True)


def validate_credit_cancellation(doc):
    """Complete conflict closure; hidden reference identities never leave this function."""
    # cancel() marks the in-memory source docstatus=2 before before_cancel.
    # Its persisted source must still be submitted; use that actual record.
    persisted = frappe.get_doc(SOURCE, doc.name, for_update=True)
    persisted.check_permission("read")

    def same(field):
        if field == "amount":
            return positive_amount(doc.get(field)) == positive_amount(persisted.get(field))
        if field == "posting_date":
            from frappe.utils import getdate

            return getdate(doc.get(field)) == getdate(persisted.get(field))
        return (doc.get(field) or None) == (persisted.get(field) or None)

    if any(not same(field) for field in SOURCE_FIELDS if field != "docstatus"):
        frappe.throw("取消前费用及应付来源已变化，请重新读取")
    journal, _, details = credit_reference_details(persisted, lock=True)
    blocked = "请先取消或撤销实际付款/核销凭证，再取消赊账费用"
    for doctype, filters, parenttype in (
        ("Payment Entry Reference", {"parenttype": "Payment Entry", "docstatus": 1,
          "reference_doctype": "Journal Entry", "reference_name": journal.name}, "Payment Entry"),
        ("Journal Entry Account", {"parenttype": "Journal Entry", "docstatus": 1,
          "reference_type": "Journal Entry", "reference_name": journal.name}, "Journal Entry"),
    ):
        for row in _locked_credit_dependencies(doctype, filters):
            if parenttype == "Journal Entry" and row.parent == journal.name:
                continue
            parent = frappe.get_doc(parenttype, row.parent, for_update=True)
            if not parent.has_permission("read"):
                frappe.throw("实际付款/核销来源不可读，不能自动取消赊账费用", frappe.PermissionError)
            if parent.docstatus == 1:
                frappe.throw(blocked)
    entries = _locked_credit_dependencies("Payment Ledger Entry", {
        "against_voucher_type": "Journal Entry", "against_voucher_no": journal.name, "delinked": 0,
    })
    external = {}
    for entry in entries:
        if (entry.company != doc.company or entry.account != doc.payable_account
                or entry.party_type != "Supplier" or entry.party != doc.supplier):
            frappe.throw(blocked)
        if entry.voucher_type != "Journal Entry" or entry.voucher_no != journal.name:
            key = (entry.voucher_type, entry.voucher_no)
            external[key] = external.get(key, Decimal(0)) + Decimal(str(entry.get("amount_in_account_currency") or 0))
    for (doctype, name), net in external.items():
        if doctype not in ("Payment Entry", "Journal Entry"):
            frappe.throw(blocked)
        parent = frappe.get_doc(doctype, name, for_update=True)
        if not parent.has_permission("read"):
            frappe.throw("实际付款/核销来源不可读，不能自动取消赊账费用", frappe.PermissionError)
        # Immutable Ledger keeps both the original and reversal PLE active.
        # Accept only a genuinely cancelled parent with exact zero net effect.
        if parent.docstatus != 2 or net != 0:
            frappe.throw(blocked)
    if (not entries or Decimal(str(details.get("outstanding_amount") or 0))
            != Decimal(str(details["total_amount"]))):
        frappe.throw(blocked)


def payment_context(name):
    doc = frappe.get_doc(SOURCE, name)
    doc.check_permission("read")
    require_fields(SOURCE, SOURCE_FIELDS)
    if not frappe.has_permission("Payment Entry", "create"):
        frappe.throw("没有新建原生付款单的权限", frappe.PermissionError)
    journal, currency, details = credit_reference_details(doc)
    if Decimal(str(details.get("outstanding_amount") or 0)) <= 0:
        frappe.throw("该赊账费用已无可付款余额，请查看原生付款及核销记录")
    return {"company": doc.company, "party_type": "Supplier", "party": doc.supplier,
            "paid_to": doc.payable_account, "paid_to_account_currency": currency,
            "reference_doctype": "Journal Entry", "reference_name": journal.name,
            "total_amount": details["total_amount"], "outstanding_amount": details["outstanding_amount"]}


def lock_expense_settlement_references(doc, method=None):
    """Serialize native new/changed PE and JE references to managed credit JEs.

    No accounting field, allocation or status is changed. Lock all sources in
    sorted order, then their JEs; an Expense cancellation uses Source -> JE too.
    Native allocation, currency, amount and reconciliation validations still run.
    """
    if doc.doctype == "Payment Entry":
        references = [(row.reference_name, row) for row in (doc.get("references") or [])
                      if row.get("reference_doctype") == "Journal Entry" and row.get("reference_name")
                      and row.get("allocated_amount")]
    elif doc.doctype == "Journal Entry":
        references = [(row.reference_name, row) for row in (doc.get("accounts") or [])
                      if row.get("reference_type") == "Journal Entry" and row.get("reference_name")]
    else:
        return
    managed = {}
    for name, row in references:
        source_name = frappe.db.get_value("Journal Entry", name, "jf_expense_voucher")
        # Internal provenance lookup only. Ordinary JE references keep their
        # existing native behavior, including when the custom field is unreadable.
        if source_name and frappe.db.get_value(SOURCE, source_name, "payment_method") == CREDIT:
            managed.setdefault(source_name, []).append((name, row))
    sources = {}
    for name in sorted(managed):
        source = frappe.get_doc(SOURCE, name, for_update=True)
        if not source.has_permission("read"):
            frappe.throw("赊账费用来源不可读或已失效，不能提交付款/核销", frappe.PermissionError)
        require_fields(SOURCE, SOURCE_FIELDS)
        if source.docstatus != 1 or source.payment_method != CREDIT:
            frappe.throw("赊账费用来源已失效，不能提交付款/核销")
        sources[name] = source
    for name in sorted(sources):
        source = sources[name]
        journal, _ = expense_journal(source, lock=True)
        for reference_name, row in managed[name]:
            if reference_name != journal.name or doc.company != source.company:
                frappe.throw("付款/核销与赊账费用的来源公司不一致")
            if doc.doctype == "Payment Entry":
                valid = (doc.payment_type == "Pay" and doc.party_type == "Supplier"
                         and doc.party == source.supplier and doc.paid_to == source.payable_account)
            else:
                valid = (row.get("party_type") == "Supplier" and row.get("party") == source.supplier
                         and row.get("account") == source.payable_account)
            if not valid:
                frappe.throw("付款/核销的供应商和应付科目必须与赊账费用一致")
