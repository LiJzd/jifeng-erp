"""Explicit tool cost assignments; native accounting remains the only ledger.

One submitted assignment owns an entire Expense Voucher. Shares are amounts,
never percentages. Native PE/GL/PLE references contain no tool identity: partial
payments therefore stay unknown per tool. Queries reject incomplete sources
rather than silently discarding hidden tools, payments or ledger rows. Two full
serial captures detect request-local changes; they are not a concurrency proof.
"""

from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
import hashlib
import json
from uuid import UUID, uuid4

import frappe
from frappe.model import get_permitted_fields

from jifeng_erp.archive_versions import same_version
from jifeng_erp.personnel_separation import _atomic


DOCTYPE = "Jifeng Tool Cost Allocation"
CHILD = "Jifeng Tool Cost Allocation Item"
EXPENSE = "Jifeng Expense Voucher"
JE, PE, GL, PLE = "Journal Entry", "Payment Entry", "GL Entry", "Payment Ledger Entry"
TOOLS = {"mold": ("Jifeng Mold", "模具", "mold_no"),
         "plate": ("Jifeng Plate", "版种", "plate_no"),
         "modeling": ("Jifeng Plate Modeling", "建模", "model_number")}
KINDS = {label: kind for kind, (_, label, _) in TOOLS.items()}
BATCH = 200
IDENTITY = {"name", "modified", "docstatus"}
ROW_ID = IDENTITY | {"parent", "parenttype", "parentfield", "idx"}
CONFIG = {"company", "expense_voucher", "reason"}
SERVER_FIELDS = {"expense_version", "journal_entry", "journal_version", "currency", "source_amount",
                 "source_date", "source_snapshot", "source_digest", "active_source_key",
                 "request_id", "request_digest", "cancel_reason"}
ITEM_FIELDS = ROW_ID | {"tool_type", "tool_doctype", "tool_name", "tool_version", "tool_label", "amount"}
EXPENSE_FIELDS = IDENTITY | {"company", "posting_date", "payment_method", "expense_account", "payment_account",
    "supplier", "payable_account", "cost_center", "amount", "journal_entry"}
JE_FIELDS = IDENTITY | {"company", "posting_date", "voucher_type", "jf_expense_voucher", "multi_currency", "finance_book"}
JE_ROW = ROW_ID | {"account", "account_currency", "exchange_rate", "party_type", "party", "cost_center",
    "debit", "credit", "debit_in_account_currency", "credit_in_account_currency", "reference_type", "reference_name"}
PE_FIELDS = IDENTITY | {"company", "posting_date", "payment_type", "party_type", "party", "paid_from", "paid_to",
    "paid_from_account_currency", "paid_to_account_currency", "paid_amount", "received_amount", "base_paid_amount",
    "base_received_amount", "source_exchange_rate", "target_exchange_rate", "total_allocated_amount",
    "unallocated_amount", "difference_amount", "book_advance_payments_in_separate_party_account"}
PE_ROW = ROW_ID | {"reference_doctype", "reference_name", "allocated_amount", "exchange_rate", "reconcile_effect_on"}
GL_FIELDS = IDENTITY | {"company", "posting_date", "account", "account_currency", "party_type", "party", "cost_center",
    "voucher_type", "voucher_no", "against_voucher_type", "against_voucher", "voucher_detail_no", "finance_book",
    "debit", "credit", "debit_in_account_currency", "credit_in_account_currency", "is_cancelled"}
PLE_FIELDS = IDENTITY | {"company", "posting_date", "account", "account_type", "account_currency", "party_type", "party",
    "voucher_type", "voucher_no", "against_voucher_type", "against_voucher_no", "voucher_detail_no", "finance_book",
    "amount", "amount_in_account_currency", "delinked"}
ACCOUNT_FIELDS = {"name", "modified", "company", "account_currency", "root_type", "account_type", "is_group"}
PARENTS = {DOCTYPE: IDENTITY | CONFIG | SERVER_FIELDS, EXPENSE: EXPENSE_FIELDS, JE: JE_FIELDS, PE: PE_FIELDS,
    GL: GL_FIELDS, PLE: PLE_FIELDS, "Company": {"name", "modified", "default_currency"},
    "Currency": {"name", "modified"}, "Account": ACCOUNT_FIELDS, "Cost Center": {"name", "modified", "company", "is_group"},
    "Supplier": {"name", "modified"},
    **{doctype: {"name", "modified", "company", title} for doctype, _, title in TOOLS.values()}}
TABLES = {DOCTYPE: {"items": (CHILD, ITEM_FIELDS)}, JE: {"accounts": ("Journal Entry Account", JE_ROW)},
    PE: {"references": ("Payment Entry Reference", PE_ROW),
         "deductions": ("Payment Entry Deduction", ROW_ID | {"account", "amount"}),
         "taxes": ("Advance Taxes and Charges", ROW_ID | {"account_head", "tax_amount"})}}
_CREATING = ContextVar("jifeng_tool_cost_creation", default=None)
_AUTHORIZING = ContextVar("jifeng_tool_cost_native_scope", default=None)
_SERVER = object()


class ToolCostWriteUncertain(RuntimeError):
    """A write whose savepoint or callback cleanup could not be verified."""


def _tool_atomic(method):
    action = _atomic(method)
    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return action(*args, **kwargs)
        except Exception as error:
            # The shared savepoint helper retains the original validation type
            # and adds a note on cleanup failure. API v1 can omit that note.
            if any("局部回滚清理失败" in note for note in getattr(error, "__notes__", [])):
                raise ToolCostWriteUncertain("工具费用保存结果尚需核实，请查询回执或单据状态") from error
            raise
    return wrapped


def _deny():
    frappe.throw("无法完整读取工具费用、分摊、会计来源或所需字段，不能返回部分金额", frappe.PermissionError)


def _permission(document, permission="read"):
    # Document.check_permission leaves a hidden source name in error_message.
    # Apply the same native role/UP/share rules without logging that identity.
    if not frappe.has_permission(document.doctype, permission, doc=document, throw=False):
        _deny()


def _invalid():
    frappe.throw("工具费用的原生来源、金额、币种或账簿不完整或不一致，请核对原生单据")


def _changed():
    frappe.throw("工具费用来源或权限已变化，请重新读取后核对")


def _name(value):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 140:
        _invalid()
    return value


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            _invalid()
    if not isinstance(value, dict):
        _invalid()
    return value


def _number(value, *, positive=False):
    if value is None or isinstance(value, bool):
        _invalid()
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        _invalid()
    if not result.is_finite() or positive and result <= 0 or abs(result) >= Decimal("1e21"):
        _invalid()
    if result.is_zero():
        return Decimal(0)
    if result.normalize().as_tuple().exponent < -9:
        _invalid()
    return result


def _text(value):
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _cell(doctype, fieldname, value):
    field = frappe.get_meta(doctype).get_field(fieldname)
    if value is not None and field and field.fieldtype in {"Currency", "Float", "Percent"}:
        return _text(_number(value))
    return _plain(value)


def _plain(value):
    if callable(getattr(value, "as_dict", None)):
        value = value.as_dict()
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return _text(value)
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(_plain(value), sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(",", ":"), default=str).encode()).hexdigest()


def _fields(doctype, fields, permission="read", parenttype=None):
    permitted = set(get_permitted_fields(doctype, permission_type=permission, parenttype=parenttype, ignore_virtual=True))
    if not set(fields).issubset(permitted):
        _deny()
    if frappe.session.user != "Administrator":
        meta = frappe.get_meta(doctype)
        levels = set(meta.get_permlevel_access(permission_type="mask", parenttype=parenttype, user=frappe.session.user))
        if any((field := meta.get_field(name)) and field.get("mask")
                and (field.permlevel or 0) not in levels for name in fields):
            _deny()


def _table(doc, name, child):
    _permission(doc)
    field = doc.meta.get_field(name)
    levels = set(doc.meta.get_permlevel_access(permission_type="read", user=frappe.session.user))
    if 0 not in levels and frappe.share.get_shared(doc.doctype, frappe.session.user, rights=["read"], limit=1):
        levels.add(0)
    if not field or field.fieldtype != "Table" or field.options != child or (field.permlevel or 0) not in levels:
        _deny()
    if frappe.session.user != "Administrator" and field.get("mask"):
        masked = set(doc.meta.get_permlevel_access(permission_type="mask", user=frappe.session.user))
        if (field.permlevel or 0) not in masked:
            _deny()


def _permissions():
    doctypes = sorted(set(PARENTS) | {child for tables in TABLES.values() for child, _ in tables.values()})
    user = frappe.session.user
    values = [("actor", user)]
    for doctype, filters, fields in (
        ("User", {"name": user}, ["name", "enabled", "user_type"]),
        ("Has Role", {"parenttype": "User", "parent": user}, ["*"]),
        ("User Permission", {"user": user}, ["*"]),
        ("DocPerm", {"parent": ["in", doctypes]}, ["*"]),
        ("Custom DocPerm", {"parent": ["in", doctypes]}, ["*"]),
        ("DocType", {"name": ["in", doctypes]}, ["*"]),
        ("DocField", {"parent": ["in", doctypes]}, ["*"]),
        ("Custom Field", {"dt": ["in", doctypes]}, ["*"]),
        ("Property Setter", {"doc_type": ["in", doctypes]}, ["*"]),
        ("DocShare", {"share_doctype": ["in", doctypes]}, ["*"]),
    ):
        rows = frappe.get_all(doctype, filters=filters, fields=fields, order_by="name asc", limit_page_length=0)
        values.append((doctype, rows))
    return _digest(values)


def _require_hooks():
    for category, expected in (
        ("has_permission", {DOCTYPE: "tool_cost_allocation_has_permission", CHILD: "tool_cost_allocation_item_has_permission"}),
        ("permission_query_conditions", {DOCTYPE: "tool_cost_allocation_query_conditions", CHILD: "tool_cost_allocation_item_query_conditions"}),
    ):
        hooks = frappe.get_hooks(category)
        for doctype, function in expected.items():
            values = hooks.get(doctype, [])
            if isinstance(values, str):
                values = [values]
            if "jifeng_erp.tool_cost_sources." + function not in values:
                frappe.throw("工具费用的原生来源权限保护尚未完整启用，不能保存或查询", frappe.PermissionError)
    events = frappe.get_hooks("doc_events").get("DocShare", {}).get("before_validate", [])
    if isinstance(events, str):
        events = [events]
    if "jifeng_erp.tool_cost_sources.prevent_tool_cost_docshare" not in events:
        frappe.throw("工具费用的原生分享权限保护尚未完整启用，不能保存或查询", frappe.PermissionError)


class _Read:
    def __init__(self, company, *, lock=False):
        if frappe.session.user == "Guest":
            _deny()
        if frappe.conf.get("read_from_replica"):
            frappe.throw("工具费用需要主库口径，请在主库核对后读取")
        _require_hooks()
        self.company, self.lock = _name(company), lock
        self.documents, self.children, self.trace = {}, {}, []
        self.permission = _permissions()
        company_doc = self.source("Company", company, select=True)
        self.currency = _name(company_doc.default_currency)
        self.source("Currency", self.currency, select=True)

    def raw(self, doctype, filters, fields):
        rows = frappe.get_all(doctype, filters=filters, fields=sorted(fields), order_by="name asc", limit_page_length=0)
        self.trace.append((doctype, filters, _plain(rows)))
        return rows

    def records(self, doctype, filters, fields, parenttype=None):
        _fields(doctype, fields, parenttype=parenttype)
        raw = self.raw(doctype, filters, fields)
        rows, names = [], set()
        while True:
            batch = frappe.get_list(doctype, filters=filters, fields=sorted(fields), order_by="name asc",
                limit_start=len(rows), limit_page_length=BATCH,
                **({"parent_doctype": parenttype} if parenttype else {}))
            for row in batch:
                if not set(fields).issubset(row) or not row.get("name") or row["name"] in names:
                    _deny()
                names.add(row["name"])
                rows.append(row)
            if len(batch) < BATCH:
                break
        if _plain(rows) != _plain(raw):
            _deny()
        return rows

    def source(self, doctype, name, *, select=False):
        _name(name)
        key = doctype, name
        if key in self.documents:
            doc = self.documents[key]
            if select:
                _permission(doc, "select")
            return doc
        token = _AUTHORIZING.set((name, frappe.session.user)) if doctype == DOCTYPE else None
        try:
            try:
                doc = frappe.get_doc(doctype, name, **({"for_update": True} if self.lock and doctype in {EXPENSE, JE} else {}))
                _permission(doc)
                if select:
                    _permission(doc, "select")
            except (frappe.PermissionError, frappe.DoesNotExistError):
                _deny()
            rows = self.records(doctype, {"name": name}, PARENTS[doctype])
        finally:
            if token is not None:
                _AUTHORIZING.reset(token)
        if len(rows) != 1 or any(_cell(doctype, field, doc.get(field)) != _cell(doctype, field, value) for field, value in rows[0].items()):
            _changed()
        if "company" in PARENTS[doctype] and doc.company != self.company:
            _deny()
        self.documents[key] = doc
        for fieldname, (child, fields) in TABLES.get(doctype, {}).items():
            _table(doc, fieldname, child)
            children = self.records(child, {"parent": name}, fields, doctype)
            # Each native parent currently has only one table of each child
            # type. Parent-only scope detects corrupt child parentfield/type.
            actual = sorted(doc.get(fieldname) or [], key=lambda row: row.name)
            if len(actual) != len(children):
                _invalid()
            for row, saved in zip(actual, children):
                if (saved["parenttype"] != doctype or saved["parentfield"] != fieldname
                        or any(_cell(child, field, row.get(field)) != _cell(child, field, value) for field, value in saved.items())):
                    _invalid()
            self.children[key + (fieldname,)] = children
        return doc

    def account(self, name, root, types=None):
        doc = self.source("Account", name)
        if (doc.company != self.company or doc.is_group or doc.root_type != root
                or doc.account_currency not in (None, "", self.currency)
                or types and doc.account_type not in types):
            _invalid()
        return doc

    def ledger(self, doctype, filters):
        records = self.records(doctype, filters, PARENTS[doctype])
        for row in records:
            doc = self.source(doctype, row.name)
            if doc.company != self.company or doc.account_currency != self.currency or doc.get("finance_book"):
                _invalid()
            _number(doc.get("amount" if doctype == PLE else "debit"))
        return records

    def reposts(self):
        pairs = {(dt, name) for dt, name in self.documents if dt in {JE, PE}}
        for parent, child, status, accepted in (
            ("Repost Payment Ledger", "Repost Payment Ledger Items", "repost_status", {"Completed"}),
            ("Repost Accounting Ledger", "Repost Accounting Ledger Items", "status", {"Completed", "Cancelled"}),
        ):
            candidates = self.raw(child, {"voucher_no": ["in", sorted(name for _, name in pairs)]},
                {"name", "parent", "parenttype", "voucher_type", "voucher_no"})
            for row in candidates:
                if (row.voucher_type, row.voucher_no) not in pairs:
                    continue
                if row.parenttype != parent:
                    _invalid()
                state = self.raw(parent, {"name": row.parent}, {"name", "docstatus", status})
                if len(state) != 1 or state[0]["docstatus"] == 1 and state[0][status] not in accepted:
                    _invalid()


def _gl_net(rows, expected, *, cancelled):
    totals = {key: Decimal(0) for key in expected}
    gross = {key: [Decimal(0), Decimal(0)] for key in expected}
    if not rows:
        _invalid()
    for row in rows:
        key = (row.account, row.get("party_type") or None, row.get("party") or None,
               row.get("against_voucher_type") or None, row.get("against_voucher") or None)
        debit, credit = _number(row.debit), _number(row.credit)
        if (key not in totals or min(debit, credit) < 0
                or debit != _number(row.debit_in_account_currency)
                or credit != _number(row.credit_in_account_currency)
                or row.is_cancelled not in (0, 1) or not cancelled and row.is_cancelled):
            _invalid()
        totals[key] += debit - credit
        gross[key][0] += debit
        gross[key][1] += credit
    if totals != {key: Decimal(0) if cancelled else amount for key, amount in expected.items()}:
        _invalid()
    if cancelled and any(values != [abs(expected[key]), abs(expected[key])] for key, values in gross.items()):
        _invalid()


def _payment(read, name, expense, journal, ledger):
    doc = read.source(PE, name)
    cancelled = doc.docstatus == 2
    if (doc.docstatus not in (1, 2) or doc.payment_type != "Pay" or doc.party_type != "Supplier"
            or doc.party != expense.supplier or doc.paid_to != expense.payable_account
            or doc.paid_from_account_currency != read.currency or doc.paid_to_account_currency != read.currency
            or doc.get("book_advance_payments_in_separate_party_account")
            or _number(doc.source_exchange_rate) != 1 or _number(doc.target_exchange_rate) != 1
            or any(_number(doc.get(field)) != 0 for field in ("difference_amount", "unallocated_amount"))
            or read.children[PE, name, "deductions"] or read.children[PE, name, "taxes"]):
        _invalid()
    read.account(doc.paid_from, "Asset", {"Cash", "Bank"})
    amount = _number(doc.paid_amount, positive=True)
    if any(_number(doc.get(field)) != amount for field in ("received_amount", "base_paid_amount", "base_received_amount",
                                                          "total_allocated_amount")):
        _invalid()
    references = read.children[PE, name, "references"]
    gl = read.ledger(GL, {"voucher_type": PE, "voucher_no": name})
    unlinked = cancelled and any(row.account == doc.paid_to and not row.get("against_voucher_type")
                                and not row.get("against_voucher") for row in gl)
    expected = {(doc.paid_from, None, None, None, None): -amount}
    allocated = Decimal(0)
    for row in references:
        _name(row.reference_doctype)
        _name(row.reference_name)
        value = _number(row.allocated_amount, positive=True)
        if _number(row.exchange_rate) != 1:
            _invalid()
        reference = (row.reference_doctype, row.reference_name)
        if unlinked:
            # Native cancellation unlinks GL references, including cancelled
            # payments, while retaining their cancelled Payment Entry children.
            # Accept this only for an exactly readable cancelled Journal Entry;
            # its whole payment must still have exact zero net and gross GL.
            if row.reference_doctype != JE:
                _invalid()
            referenced = read.source(JE, row.reference_name)
            if referenced.docstatus == 2:
                reference = (None, None)
        key = (doc.paid_to, "Supplier", expense.supplier, *reference)
        expected[key] = expected.get(key, Decimal(0)) + value
        if row.reference_doctype == JE and row.reference_name == journal.name:
            allocated += value
    if sum(_number(row.allocated_amount, positive=True) for row in references) != amount or allocated <= 0:
        _invalid()
    _gl_net(gl, expected, cancelled=cancelled)
    actual = sum(_number(row.amount_in_account_currency) for row in ledger
                 if row.voucher_type == PE and row.voucher_no == name and not row.delinked)
    if actual != (Decimal(0) if cancelled else -allocated):
        _invalid()
    return {"name": name, "version": _plain(doc.modified), "posting_date": _plain(doc.posting_date),
            "docstatus": int(doc.docstatus), "allocated_amount": _text(allocated),
            "actual_paid": _text(Decimal(0) if cancelled else allocated)}


def _source(read, name):
    expense = read.source(EXPENSE, name, select=True)
    cancelled = expense.docstatus == 2
    if expense.docstatus not in (1, 2) or expense.payment_method not in {"现金", "银行转账", "其他", "赊账"}:
        _invalid()
    amount = _number(expense.amount, positive=True)
    journal = read.source(JE, expense.journal_entry)
    if (journal.docstatus != expense.docstatus or journal.jf_expense_voucher != expense.name
            or journal.voucher_type != "Journal Entry" or journal.multi_currency or journal.get("finance_book")
            or str(journal.posting_date) != str(expense.posting_date)):
        _invalid()
    read.account(expense.expense_account, "Expense")
    center = read.source("Cost Center", expense.cost_center)
    if center.is_group:
        _invalid()
    credit = expense.payment_method == "赊账"
    offset = expense.payable_account if credit else expense.payment_account
    read.account(offset, "Liability" if credit else "Asset", {"Payable"} if credit else {"Cash", "Bank"})
    if not credit and expense.payment_method in {"现金", "银行转账"}:
        if read.documents["Account", offset].account_type != {"现金": "Cash", "银行转账": "Bank"}[expense.payment_method]:
            _invalid()
    if credit:
        read.source("Supplier", expense.supplier, select=True)
    rows = read.children[JE, journal.name, "accounts"]
    if len(rows) != 2:
        _invalid()
    # Native child queries are ordered by name, not accounting position.
    debits = [row for row in rows if row.account == expense.expense_account]
    offsets = [row for row in rows if row.account == offset]
    if len(debits) != 1 or len(offsets) != 1:
        _invalid()
    debit, counterpart = debits[0], offsets[0]
    for row in rows:
        if (row.account_currency != read.currency or _number(row.exchange_rate) != 1
                or row.get("reference_type") or row.get("reference_name")
                or _number(row.debit) != _number(row.debit_in_account_currency)
                or _number(row.credit) != _number(row.credit_in_account_currency)):
            _invalid()
    if (debit.cost_center != expense.cost_center or _number(debit.debit) != amount or _number(debit.credit) != 0
            or debit.get("party_type") or debit.get("party")
            or _number(counterpart.credit) != amount or _number(counterpart.debit) != 0
            or (counterpart.get("party_type") or None, counterpart.get("party") or None)
                != (("Supplier", expense.supplier) if credit else (None, None))):
        _invalid()
    gl = read.ledger(GL, {"voucher_type": JE, "voucher_no": journal.name})
    _gl_net(gl, {(expense.expense_account, None, None, None, None): amount,
                 (offset, "Supplier" if credit else None, expense.supplier if credit else None, None, None): -amount},
            cancelled=cancelled)
    if not cancelled and any(str(row.posting_date) != str(expense.posting_date) for row in gl):
        _invalid()
    if any(row.account == expense.expense_account and row.get("cost_center") != expense.cost_center for row in gl):
        _invalid()
    ledger = read.ledger(PLE, {"against_voucher_type": JE, "against_voucher_no": journal.name})
    payments = []
    if credit:
        if not ledger:
            _invalid()
        for row in ledger:
            if (row.account != offset or row.account_type != "Payable" or row.party_type != "Supplier"
                    or row.party != expense.supplier or row.delinked not in (0, 1)
                    or _number(row.amount) != _number(row.amount_in_account_currency)
                    or row.voucher_type not in {JE, PE} or row.voucher_type == JE and row.voucher_no != journal.name):
                _invalid()
        base = sum(_number(row.amount_in_account_currency) for row in ledger
                   if not row.delinked and row.voucher_type == JE and row.voucher_no == journal.name)
        if base != (Decimal(0) if cancelled else amount):
            _invalid()
        references = read.records("Payment Entry Reference", {"reference_doctype": JE,
            "reference_name": journal.name, "docstatus": ["in", [1, 2]]}, PE_ROW, PE)
        names = {row.parent for row in references} | {row.voucher_no for row in ledger if row.voucher_type == PE}
        for row in references:
            if row.parenttype != PE or row.parentfield != "references":
                _invalid()
        payments = [_payment(read, name, expense, journal, ledger) for name in sorted(names)]
        paid = sum(_number(row["actual_paid"]) for row in payments)
        unpaid = sum(_number(row.amount_in_account_currency) for row in ledger if not row.delinked)
        if paid < 0 or paid > amount or unpaid != (Decimal(0) if cancelled else amount - paid):
            _invalid()
        if cancelled and paid:
            _invalid()
    else:
        if ledger:
            _invalid()
        paid, unpaid = (Decimal(0), Decimal(0)) if cancelled else (amount, Decimal(0))
    read.reposts()
    facts = {"expense": {field: _cell(EXPENSE, field, expense.get(field)) for field in EXPENSE_FIELDS - IDENTITY},
             "journal": {field: _cell(JE, field, journal.get(field)) for field in JE_FIELDS - IDENTITY},
             "accounts": [{field: _cell("Journal Entry Account", field, row.get(field)) for field in JE_ROW - ROW_ID} for row in rows]}
    return {"company": read.company, "expense_voucher": expense.name, "expense_version": _plain(expense.modified),
            "journal_entry": journal.name, "journal_version": _plain(journal.modified), "currency": read.currency,
            "posting_date": _plain(expense.posting_date), "historical_amount": _text(amount),
            "incurred_amount": _text(Decimal(0) if cancelled else amount), "paid_amount": _text(paid),
            "unpaid_amount": _text(unpaid), "source_state": "cancelled" if cancelled else "submitted",
            "payment_basis": "native_cash_bank_gl" if not credit else "native_payment_entry_gl_ple",
            "payments": payments, "facts": facts}


def _stable(company, build, *, lock=False):
    first = _Read(company, lock=lock)
    result = build(first)
    first_state = _digest((result, first.trace))
    if first.permission != _permissions():
        _changed()
    second = _Read(company, lock=lock)
    again = build(second)
    if first.permission != second.permission or second.permission != _permissions() or first_state != _digest((again, second.trace)):
        _changed()
    return result


def _public(source):
    return {key: value for key, value in source.items() if key != "facts"}


@frappe.whitelist(methods=["GET"])
def get_tool_cost_source(context):
    context = _object(context)
    if set(context) != {"company", "expense_voucher"}:
        _invalid()
    return _public(_stable(context["company"], lambda read: _source(read, context["expense_voucher"])))


def _items(read, rows, *, expected=None):
    if not rows or len(rows) > 100:
        frappe.throw("请明确填写一至一百个工具的分摊金额")
    result, seen = [], set()
    for row in rows:
        kind = KINDS.get(row.get("tool_type"))
        if kind not in TOOLS:
            _invalid()
        doctype, _, title = TOOLS[kind]
        name = _name(row.get("tool_name"))
        if (doctype, name) in seen or row.get("tool_doctype") not in (None, "", doctype):
            frappe.throw("同一费用不能重复分摊到同一个工具")
        seen.add((doctype, name))
        doc = read.source(doctype, name, select=True)
        if expected is not None and (doctype, name) in expected and not same_version(doc.modified, expected[doctype, name]):
            _changed()
        amount = _number(row.get("amount"), positive=True)
        if len(_text(amount)) > 130:
            _invalid()
        result.append({"tool_type": row.get("tool_type"), "tool_doctype": doctype, "tool_name": name,
                       "tool_version": _plain(doc.modified), "tool_label": doc.get(title), "amount": _text(amount)})
    return result


def _prepare(document, *, expected=None, lock=False):
    def build(read):
        source = _source(read, document.expense_voucher)
        if source["source_state"] != "submitted":
            frappe.throw("只能正式分摊已提交且原生账簿完整的费用")
        if expected and not same_version(source["expense_version"], expected["expense"]):
            _changed()
        items = _items(read, document.get("items") or [], expected=expected["tools"] if expected else None)
        total = sum(_number(row["amount"]) for row in items)
        if total != _number(source["historical_amount"]):
            frappe.throw("各工具明确分摊金额之和必须准确等于整笔原生费用，不允许少分或超分")
        precision = read.documents[EXPENSE, document.expense_voucher].precision("amount")
        if isinstance(precision, bool) or not isinstance(precision, int) or not 0 <= precision <= 9:
            _invalid()
        quantum = Decimal(1).scaleb(-precision)
        for row in items:
            try:
                if _number(row["amount"]).quantize(quantum) != _number(row["amount"]):
                    _invalid()
            except InvalidOperation:
                _invalid()
        return source, items
    return _stable(document.company, build, lock=lock)


def before_insert_allocation(document):
    if frappe.session.user == "Guest":
        _deny()
    _permission(document, "create")
    # Native Link defaults can fill Currency from the reader's Company UP.
    # Always derive it again from the exact Expense; never trust the default.
    document.currency = None
    if any(document.get(field) not in (None, "") for field in SERVER_FIELDS):
        frappe.throw("工具费用来源快照和请求身份只能由服务器保存")
    if any(row.get(field) not in (None, "") for row in document.get("items") or []
           for field in ("tool_version", "tool_label")):
        frappe.throw("工具版本和名称快照只能由服务器保存")
    creating = _CREATING.get()
    document.request_id = creating[1] if creating and creating[0] is document else str(uuid4())
    document.request_digest = creating[2] if creating and creating[0] is document else _digest({
        "company": document.company, "expense_voucher": document.expense_voucher, "reason": document.reason,
        "items": [{field: row.get(field) for field in ("tool_type", "tool_name", "amount")} for row in document.get("items") or []]})
    document.active_source_key = None
    document.__dict__["_jf_tool_cost_server"] = _SERVER


def _content(document):
    return {"parent": {field: document.get(field) for field in CONFIG | SERVER_FIELDS - {"active_source_key", "cancel_reason"}},
            "items": [{field: row.get(field) for field in ITEM_FIELDS - {"modified", "docstatus"}}
                      for row in document.get("items") or []]}


def validate_allocation(document):
    if frappe.session.user == "Guest":
        _deny()
    _permission(document)
    _fields(DOCTYPE, CONFIG | SERVER_FIELDS | IDENTITY)
    _fields(DOCTYPE, CONFIG, "write")
    _table(document, "items", CHILD)
    _fields(CHILD, ITEM_FIELDS, parenttype=DOCTYPE)
    _fields(CHILD, {"tool_type", "tool_doctype", "tool_name", "amount"}, "write", DOCTYPE)
    if not isinstance(document.reason, str) or not document.reason.strip() or len(document.reason) > 1000:
        frappe.throw("请填写明确的工具费用归属与分摊依据")
    before = None if document.is_new() else document.get_doc_before_save()
    if before and before.docstatus == 1:
        if _digest(_content(document)) != _digest(_content(before)):
            frappe.throw("正式工具费用分摊不能修改，请撤销后重新明确分摊")
        return
    if document.docstatus not in (0, 1):
        _invalid()
    if document.is_new():
        if document.__dict__.get("_jf_tool_cost_server") is not _SERVER:
            _invalid()
    # Native Form JSON carries Date as ISO text; MariaDB reads it as date.
    # Compare the same canonical values while still rejecting any actual edit.
    elif not before or any(_plain(document.get(field)) != _plain(before.get(field)) for field in SERVER_FIELDS):
        frappe.throw("工具费用来源快照和请求身份不能直接修改")
    if before:
        if document.company != before.company:
            frappe.throw("工具费用分摊的公司不能修改")
        old_rows = {row.name: row for row in before.get("items") or []}
        for row in document.get("items") or []:
            old = old_rows.get(row.name)
            if old and any(row.get(field) != old.get(field) for field in ("tool_version", "tool_label")):
                frappe.throw("工具版本和名称快照不能直接修改")
    creating = _CREATING.get()
    expected = creating[3] if creating and creating[0] is document else None
    if before and before.expense_voucher == document.expense_voucher:
        expected = {"expense": before.expense_version, "tools": {
            (row.tool_doctype, row.tool_name): row.tool_version for row in before.get("items") or []}}
        if document.docstatus == 0:
            # Explicit native draft save rereads each selected tool. Submission
            # still requires the last saved tool versions, with no silent rebase.
            expected["tools"] = {}
        # New explicitly selected tools receive their first snapshot on save.
        expected["tools"] = {key: value for key, value in expected["tools"].items()
            if any((TOOLS[KINDS[row.tool_type]][0], row.tool_name) == key for row in document.get("items") or []
                   if row.tool_type in KINDS)}
    source, items = _prepare(document, expected=expected, lock=True)
    document.expense_version, document.journal_entry, document.journal_version = (
        source["expense_version"], source["journal_entry"], source["journal_version"])
    document.currency, document.source_amount, document.source_date = source["currency"], source["historical_amount"], source["posting_date"]
    document.source_snapshot = json.dumps(source["facts"], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    document.source_digest = _digest(source["facts"])
    for row, snapshot in zip(document.get("items"), items):
        for field, value in snapshot.items():
            row.set(field, value)


def before_submit_allocation(document):
    _permission(document, "submit")
    source, items = _prepare(document, expected={"expense": document.expense_version,
        "tools": {(row.tool_doctype, row.tool_name): row.tool_version for row in document.get("items")}}, lock=True)
    if document.source_digest != _digest(source["facts"]) or document.source_amount != source["historical_amount"]:
        _changed()
    if any(row.amount != expected["amount"] or row.tool_version != expected["tool_version"]
           for row, expected in zip(document.get("items"), items)):
        _changed()
    key = _digest({"company": document.company, "expense_voucher": document.expense_voucher})
    # Source is already locked. Never lock another allocation after it: native
    # save/cancel first locks its own allocation, then this same Expense source.
    existing = frappe.db.get_value(DOCTYPE, {"active_source_key": key}, "name")
    if existing and existing != document.name:
        frappe.throw("该整笔费用已有正式工具分摊，请查询核对，不能重复分摊")
    document.active_source_key = key


def prevent_allocation_update(document):
    frappe.throw("正式工具费用分摊不能修改，请撤销后重新明确分摊")


def before_cancel_allocation(document):
    _permission(document, "cancel")
    before = document.get_doc_before_save()
    if (not before or before.docstatus != 1 or _digest(_content(document)) != _digest(_content(before))
            or document.active_source_key != before.active_source_key):
        _changed()
    if not isinstance(document.cancel_reason, str) or not document.cancel_reason.strip() or len(document.cancel_reason) > 1000:
        frappe.throw("请明确填写撤销工具费用分摊的原因；撤销不会取消费用或付款")
    _stable(document.company, lambda read: _source(read, document.expense_voucher), lock=True)
    document.active_source_key = None


def prevent_allocation_delete(document):
    frappe.throw("工具费用分摊及创建回执必须保留追溯，不能删除")


def prevent_allocation_rename(document, *args, **kwargs):
    frappe.throw("工具费用分摊记录不能改名或合并")


def prevent_allocation_share(document, share):
    # Frappe shares can override both document and List permission hooks.
    # DocShare.validate invokes this controller gate, including Administrator
    # and native add_docshare(ignore_share_permission=True).
    frappe.throw("工具费用分摊必须按原生财务来源权限读取，不能单独分享", frappe.PermissionError)


def prevent_tool_cost_docshare(document, method=None):
    # before_validate still runs when native add_docshare receives ignore_validate.
    if document.get("share_doctype") in {DOCTYPE, CHILD}:
        prevent_allocation_share(document, document)


def _allocation_view(read, name):
    doc = read.source(DOCTYPE, name)
    expected_key = _digest({"company": doc.company, "expense_voucher": doc.expense_voucher}) if doc.docstatus == 1 else None
    if doc.docstatus not in (0, 1, 2) or (doc.active_source_key or None) != expected_key:
        _invalid()
    source = _source(read, doc.expense_voucher)
    if doc.source_digest != _digest(source["facts"]) or doc.source_amount != source["historical_amount"]:
        _changed()
    if doc.currency != source["currency"] or doc.journal_entry != source["journal_entry"]:
        _changed()
    rows = read.children[DOCTYPE, name, "items"]
    if sorted(row.idx for row in rows) != list(range(1, len(rows) + 1)):
        _invalid()
    rows = sorted(rows, key=lambda row: row.idx)
    if sum(_number(row.amount, positive=True) for row in rows) != _number(source["historical_amount"]):
        _invalid()
    items, seen = [], set()
    for row in rows:
        kind = KINDS.get(row.tool_type)
        if not kind or row.tool_doctype != TOOLS[kind][0] or (row.tool_doctype, row.tool_name) in seen:
            _invalid()
        seen.add((row.tool_doctype, row.tool_name))
        read.source(row.tool_doctype, row.tool_name)
        official = doc.docstatus == 1 and source["source_state"] == "submitted"
        amount = _number(row.amount, positive=True)
        if source["source_state"] == "cancelled":
            paid, unpaid, state = "0", "0", "source_cancelled"
        elif not official:
            paid, unpaid, state = None, None, "not_active"
        elif _number(source["paid_amount"]) == 0:
            paid, unpaid, state = "0", _text(amount), "no_payment"
        elif _number(source["unpaid_amount"]) == 0:
            paid, unpaid, state = _text(amount), "0", "fully_paid"
        else:
            paid, unpaid, state = None, None, "partial_payment_tool_unknown"
        items.append({"record_type": kind, "tool_doctype": row.tool_doctype, "tool_name": row.tool_name,
            "tool_version": row.tool_version, "tool_label": row.tool_label, "historical_amount": _text(amount),
            "incurred_amount": _text(amount) if official else "0" if doc.docstatus == 2 or source["source_state"] == "cancelled" else None,
            "paid_amount": paid, "unpaid_amount": unpaid, "payment_state": state,
            "payment_reason": "原生付款只引用整笔费用的总账凭证，没有逐工具付款分配；部分付款不能拆到工具"
                if state == "partial_payment_tool_unknown" else None})
    return {"name": doc.name, "version": _plain(doc.modified), "company": doc.company, "docstatus": int(doc.docstatus),
            "reason": doc.reason, "cancel_reason": doc.cancel_reason, "currency": doc.currency,
            "can_cancel": doc.docstatus == 1 and bool(doc.has_permission("cancel")),
            "source": _public(source), "items": items}


@frappe.whitelist(methods=["GET"])
def get_tool_cost_allocation(name, version=None):
    doc = frappe.get_doc(DOCTYPE, _name(name))
    _permission(doc)
    _fields(DOCTYPE, {"name", "modified", "company"})
    if version is not None and not same_version(doc.modified, version):
        _changed()
    return _stable(doc.company, lambda read: _allocation_view(read, name))


@frappe.whitelist(methods=["GET"])
def get_tool_costs(company, record_type, name):
    if record_type not in TOOLS:
        _invalid()
    doctype = TOOLS[record_type][0]
    def build(read):
        read.source(doctype, _name(name))
        candidates = read.records(CHILD, {"tool_doctype": doctype, "tool_name": name,
            "docstatus": ["in", [1, 2]]}, ITEM_FIELDS, DOCTYPE)
        for row in candidates:
            if row.parenttype != DOCTYPE or row.parentfield != "items":
                _invalid()
        allocations = [_allocation_view(read, parent) for parent in sorted({row.parent for row in candidates})]
        active = [row for allocation in allocations for row in allocation["items"]
                  if row["tool_doctype"] == doctype and row["tool_name"] == name and allocation["docstatus"] == 1]
        return {"company": company, "record_type": record_type, "name": name, "currency": read.currency,
            "can_create": bool(frappe.has_permission(DOCTYPE, "create")),
            "incurred_amount": _text(sum(_number(row["incurred_amount"]) for row in active)),
            "paid_amount": None if any(row["paid_amount"] is None for row in active) else _text(sum(_number(row["paid_amount"]) for row in active)),
            "unpaid_amount": None if any(row["unpaid_amount"] is None for row in active) else _text(sum(_number(row["unpaid_amount"]) for row in active)),
            "allocations": allocations}
    return _stable(company, build)


def _native_guard(document):
    _fields(DOCTYPE, PARENTS[DOCTYPE])
    token = _AUTHORIZING.set((document.name, frappe.session.user))
    try:
        _table(document, "items", CHILD)
        _fields(CHILD, ITEM_FIELDS, parenttype=DOCTYPE)
    finally:
        _AUTHORIZING.reset(token)
    def build(read):
        source = _source(read, document.expense_voucher)
        for row in document.get("items") or []:
            kind = KINDS.get(row.get("tool_type"))
            if not kind or row.tool_doctype != TOOLS[kind][0]:
                _invalid()
            read.source(row.tool_doctype, row.tool_name)
        if document.source_digest and document.source_digest != _digest(source["facts"]):
            _changed()
        return _public(source)
    return _stable(document.company, build)


def tool_cost_allocation_has_permission(doc, ptype, user=None, debug=False):
    """Deny-only native hook. An exact internal name token prevents recursion.

    Frappe still applies ordinary roles, Company/source UP and shares after this
    hook; returning True here does not grant a missing native permission.
    """
    user = user or frappe.session.user
    if user != frappe.session.user or user == "Guest":
        return False
    if _AUTHORIZING.get() == (doc.name, user):
        return True
    if doc.is_new():
        return ptype in {"read", "create", "write", "select"}
    try:
        _native_guard(doc)
        return True
    except (frappe.PermissionError, frappe.DoesNotExistError, frappe.ValidationError):
        return False


def _native_allowed(user):
    if user != frappe.session.user or user == "Guest":
        return []
    token = _AUTHORIZING.get()
    if token and token[1] == user:
        return [token[0]]
    rows = frappe.get_all(DOCTYPE, fields=["name"], order_by="name asc", limit_page_length=0)
    allowed = []
    for row in rows:
        document = frappe.get_doc(DOCTYPE, row.name)
        if tool_cost_allocation_has_permission(document, "read", user):
            allowed.append(row.name)
    return allowed


def tool_cost_allocation_query_conditions(user=None, doctype=None):
    allowed = _native_allowed(user or frappe.session.user)
    return "`tabJifeng Tool Cost Allocation`.`name` in (" + ",".join(frappe.db.escape(name) for name in allowed) + ")" if allowed else "1=0"


def tool_cost_allocation_item_query_conditions(user=None, doctype=None):
    allowed = _native_allowed(user or frappe.session.user)
    if not allowed:
        return "1=0"
    return ("`tabJifeng Tool Cost Allocation Item`.`parenttype`='Jifeng Tool Cost Allocation' and "
            "`tabJifeng Tool Cost Allocation Item`.`parentfield`='items' and "
            "`tabJifeng Tool Cost Allocation Item`.`parent` in (" + ",".join(frappe.db.escape(name) for name in allowed) + ")")


def tool_cost_allocation_item_has_permission(doc, ptype, user=None, debug=False):
    if doc.get("parenttype") != DOCTYPE or doc.get("parentfield") != "items":
        return False
    try:
        parent = frappe.get_doc(DOCTYPE, doc.parent)
        return parent.has_permission(ptype, user=user or frappe.session.user)
    except (frappe.PermissionError, frappe.DoesNotExistError):
        return False


def _uuid(value):
    try:
        identity = UUID(value) if isinstance(value, str) else None
    except (ValueError, AttributeError):
        identity = None
    if not identity or identity.version != 4 or str(identity) != value:
        frappe.throw("请提供有效的创建请求UUID4身份")
    return value


def _receipt(company, request_id):
    read = _Read(company)
    rows = read.records(DOCTYPE, {"company": company, "request_id": request_id},
                        {"name", "modified", "company", "docstatus", "request_id", "request_digest"})
    if len(rows) > 1:
        _invalid()
    if not rows:
        return None
    doc = frappe.get_doc(DOCTYPE, rows[0].name)
    _permission(doc)
    if any(_plain(doc.get(field)) != _plain(value) for field, value in rows[0].items()):
        _changed()
    return {"name": doc.name, "version": _plain(doc.modified), "company": doc.company,
            "docstatus": int(doc.docstatus), "request_id": doc.request_id, "request_digest": doc.request_digest}


@frappe.whitelist(methods=["GET"])
def resolve_tool_cost_creation(company, request_id):
    return _receipt(_name(company), _uuid(request_id))


@frappe.whitelist(methods=["POST"])
@_tool_atomic
def create_tool_cost_allocation(context, items, request_id):
    context = _object(context)
    if set(context) != {"company", "expense_voucher", "expense_version", "reason"}:
        _invalid()
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except (ValueError, TypeError):
            _invalid()
    if not isinstance(items, list) or not items or len(items) > 100:
        _invalid()
    expected, rows = {"expense": context["expense_version"], "tools": {}}, []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"record_type", "name", "version", "amount"} or item["record_type"] not in TOOLS:
            _invalid()
        doctype, label, _ = TOOLS[item["record_type"]]
        if not isinstance(item["amount"], str):
            _invalid()
        expected["tools"][doctype, _name(item["name"])] = item["version"]
        rows.append({"tool_type": label, "tool_doctype": doctype, "tool_name": item["name"], "amount": _text(_number(item["amount"], positive=True))})
    request_id = _uuid(request_id)
    digest = _digest({"context": context, "items": items})
    receipt = _receipt(context["company"], request_id)
    if receipt:
        if receipt["request_digest"] != digest:
            frappe.throw("该创建请求已使用，请查询核对，不能用同一身份保存另一份分摊")
        return receipt
    doc = frappe.get_doc({"doctype": DOCTYPE, "company": context["company"], "expense_voucher": context["expense_voucher"],
                          "reason": context["reason"], "items": rows})
    token = _CREATING.set((doc, request_id, digest, expected))
    try:
        doc.insert()
    finally:
        _CREATING.reset(token)
    return _receipt(context["company"], request_id)


@frappe.whitelist(methods=["POST"])
@_tool_atomic
def cancel_tool_cost_allocation(name, version, reason):
    doc = frappe.get_doc(DOCTYPE, _name(name), for_update=True)
    _permission(doc)
    _permission(doc, "cancel")
    _fields(DOCTYPE, CONFIG | SERVER_FIELDS | IDENTITY)
    if doc.docstatus != 1 or not same_version(doc.modified, version):
        _changed()
    doc.cancel_reason = reason
    doc.cancel()
    return {"name": doc.name, "version": _plain(doc.modified), "company": doc.company, "docstatus": int(doc.docstatus)}
