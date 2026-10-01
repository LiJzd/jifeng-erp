"""Read native cutoff payables only after authorizing the complete ledger scope.

No settlement, payment creation, legacy classification or balance arithmetic.
Cancelled vouchers can have active historical/reversing entries in the immutable
ledger; their native posting dates and amounts are preserved. Hidden payments,
accounts or counterpart vouchers cause rejection of the whole query.
"""

from decimal import Decimal, InvalidOperation

import frappe
from frappe.utils import getdate


REPORT = "Accounts Payable"
PLE = "Payment Ledger Entry"
ACCOUNT_FIELDS = ("name", "company", "account_type", "account_currency", "is_group")
LEDGER_FIELDS = ("name", "company", "account", "account_type", "account_currency", "party_type", "party",
                 "posting_date", "voucher_type", "voucher_no", "against_voucher_type", "against_voucher_no",
                 "amount", "amount_in_account_currency", "due_date", "delinked")
SOURCE_FIELDS = {
    "Purchase Invoice": ("name", "company", "docstatus", "posting_date", "supplier", "credit_to",
                         "is_return", "return_against", "due_date", "currency", "conversion_rate",
                         "grand_total", "base_grand_total", "rounded_total", "base_rounded_total", "outstanding_amount"),
    "Payment Entry": ("name", "company", "docstatus", "posting_date", "party_type", "party",
                      "payment_type", "paid_from", "paid_to", "paid_amount", "received_amount",
                      "paid_from_account_currency", "paid_to_account_currency", "source_exchange_rate", "target_exchange_rate"),
    "Journal Entry": ("name", "company", "docstatus", "posting_date", "due_date", "multi_currency"),
    "Purchase Order": ("name", "company", "docstatus", "transaction_date", "supplier"),
}
JOURNAL_ROW_FIELDS = ("name", "account", "party_type", "party", "debit_in_account_currency",
                      "credit_in_account_currency", "reference_type", "reference_name")
ROW_FIELDS = ("posting_date", "due_date", "party_type", "party", "party_account", "voucher_type",
              "voucher_no", "currency", "invoiced", "paid", "credit_note", "outstanding")
MONEY_FIELDS = ("invoiced", "paid", "credit_note", "outstanding")
REQUIRED_ROW_FIELDS = set(ROW_FIELDS) - {"due_date"}


def _deny():
    frappe.throw("无法完整读取所选供应商的应付科目、账簿或凭证来源，不能提供部分应付余额", frappe.PermissionError)


def _invalid():
    frappe.throw("应付账簿与来源凭证不完整或范围不一致，请先核对原生账簿")


def _unmasked(doctype, fields, parenttype=None):
    # AP uses amount aliases: masking a source field in the native report's
    # ref_doctype cannot reliably mask the derived balance. Follow Meta's mask
    # permission rule from current fields, without its separate cached list.
    if frappe.session.user == "Administrator":
        return
    meta = frappe.get_meta(doctype)
    levels = set(meta.get_permlevel_access(permission_type="mask", parenttype=parenttype, user=frappe.session.user))
    for field in fields:
        df = meta.get_field(field)
        if df and df.get("mask") and (df.permlevel or 0) not in levels:
            _deny()


def _fields(doctype, fields, parenttype=None):
    from frappe.model import get_permitted_fields

    permitted = set(get_permitted_fields(doctype, parenttype=parenttype, permission_type="read"))
    if not set(fields).issubset(permitted):
        _deny()
    _unmasked(doctype, fields, parenttype)


def _table(doctype, fieldname):
    meta = frappe.get_meta(doctype)
    field = meta.get_field(fieldname)
    levels = meta.get_permlevel_access(permission_type="read", user=frappe.session.user)
    if not field or field.fieldtype != "Table" or (field.permlevel or 0) not in levels:
        _deny()
    _unmasked(doctype, (fieldname,))


def _document(doctype, name, fields, *, select=False):
    if not name:
        _invalid()
    try:
        doc = frappe.get_doc(doctype, name)
    except frappe.DoesNotExistError:
        _invalid()
    if not doc.has_permission("read") or (select and not doc.has_permission("select")):
        _deny()
    _fields(doctype, fields)
    return doc


def _visible_names(doctype, query):
    try:
        return set(frappe.get_list(doctype, filters=query, pluck="name", limit_page_length=0))
    except frappe.PermissionError:
        _deny()


def _complete_records(doctype, query, fields):
    # The raw lookup is internal and bounded by an already authorized party and
    # company. Its names/rows are never returned when a permission is missing.
    _fields(doctype, fields)
    records = frappe.get_all(doctype, filters=query, fields=["*"], limit_page_length=0)
    if {row.name for row in records} != _visible_names(doctype, query):
        _deny()
    for row in records:
        if not frappe.get_doc({**row, "doctype": doctype}).has_permission("read"):
            _deny()
    return records


def _money(value):
    if value is None or isinstance(value, bool):
        _invalid()
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        _invalid()
    if not amount.is_finite():
        _invalid()
    # Text representation only: no allocation, rounding or balance calculation.
    return str(amount)


def _freeze(value):
    """Detach an internal state from mutable documents without exposing it."""
    if callable(getattr(value, "as_dict", None)):
        value = value.as_dict()
    if isinstance(value, dict):
        return tuple((key, _freeze(item)) for key, item in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    return type(value).__name__, repr(value)


def _records_state(records):
    return tuple(sorted((_freeze(row) for row in records), key=repr))


def _scope(company, supplier, report_date, payable_account):
    for value in (company, supplier, report_date):
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            frappe.throw("请选择单一公司、供应商和明确的截止日期")
    if payable_account is not None and (not isinstance(payable_account, str)
            or not payable_account.strip() or payable_account != payable_account.strip()):
        frappe.throw("请选择明确的应付科目，或不指定科目")
    try:
        cutoff = getdate(report_date)
    except (TypeError, ValueError):
        frappe.throw("截止日期必须为有效的 YYYY-MM-DD 日期")
    if str(cutoff) != report_date:
        frappe.throw("截止日期必须为明确的 YYYY-MM-DD 日期")
    # Native Company selection is sufficient to derive its business currency,
    # matching the existing order-material workbench. No company profile is
    # returned, and no new Company read permission is granted.
    if company not in _visible_names("Company", {"name": company}):
        _deny()
    try:
        company_doc = frappe.get_doc("Company", company)
    except frappe.DoesNotExistError:
        _invalid()
    if not company_doc.has_permission("select"):
        _deny()
    supplier_doc = _document("Supplier", supplier, ("name",), select=True)
    currency = company_doc.default_currency
    _unmasked("Company", ("default_currency",))
    if not currency:
        frappe.throw("请先配置公司本币")
    currency_doc = _document("Currency", currency, ("name",), select=True)
    query = {"company": company, "account_type": "Payable", "is_group": 0}
    if payable_account:
        query["name"] = payable_account
    accounts = {row.name: row for row in _complete_records("Account", query, ACCOUNT_FIELDS)}
    if not accounts or (payable_account and payable_account not in accounts):
        _invalid()
    for account in accounts.values():
        doc = _document("Account", account.name, ACCOUNT_FIELDS, select=bool(payable_account))
        if doc.company != company or doc.account_type != "Payable" or doc.is_group:
            _invalid()
        if payable_account and (doc.account_currency or currency) != currency:
            frappe.throw("指定应付科目时只支持与公司本币一致的科目")
    masters = {("Company", company): {"name": company, "default_currency": currency},
               ("Supplier", supplier): supplier_doc, ("Currency", currency): currency_doc}
    masters.update((('Account', doc.name), doc) for doc in accounts.values())
    return cutoff, currency, accounts, masters


def _source(doctype, name, account, company, supplier, sources, visiting):
    if doctype not in SOURCE_FIELDS:
        _invalid()
    key = (doctype, name, account)
    if key in visiting:
        _invalid()
    if key in sources:
        return sources[key]
    visiting.add(key)
    try:
        doc = _document(doctype, name, SOURCE_FIELDS[doctype])
        if doc.company != company or doc.docstatus not in (1, 2):
            _invalid()
        if doctype == "Purchase Invoice":
            if doc.supplier != supplier or doc.credit_to != account:
                _invalid()
            if doc.is_return and doc.return_against:
                _source(doctype, doc.return_against, account, company, supplier, sources, visiting)
        elif doctype == "Purchase Order":
            if doc.supplier != supplier:
                _invalid()
        elif doctype == "Payment Entry":
            if doc.party_type != "Supplier" or doc.party != supplier:
                _invalid()
            party_account = {"Pay": doc.paid_to, "Receive": doc.paid_from}.get(doc.payment_type)
            if party_account != account:
                _invalid()
        elif doctype == "Journal Entry":
            _table(doctype, "accounts")
            _fields("Journal Entry Account", JOURNAL_ROW_FIELDS, parenttype=doctype)
            if not any(row.account == account and row.party_type == "Supplier" and row.party == supplier
                       for row in doc.accounts):
                _invalid()
            if doc.get("jf_expense_voucher"):
                from jifeng_erp.accounting import CREDIT, SOURCE, SOURCE_FIELDS as EXPENSE_FIELDS

                _fields(doctype, ("jf_expense_voucher",))
                expense = _document(SOURCE, doc.jf_expense_voucher, EXPENSE_FIELDS)
                if (expense.company != company or expense.docstatus not in (1, 2)
                        or expense.docstatus != doc.docstatus
                        or expense.payment_method != CREDIT or expense.supplier != supplier
                        or expense.payable_account != account or expense.journal_entry != doc.name):
                    _invalid()
        sources[key] = doc
        return doc
    finally:
        visiting.remove(key)


def _ledger_closure(company, supplier, cutoff, currency, accounts):
    query = {"company": company, "party_type": "Supplier", "party": supplier,
             "account": ["in", sorted(accounts)], "delinked": 0, "posting_date": ["<=", str(cutoff)]}
    records = _complete_records(PLE, query, LEDGER_FIELDS)
    sources = {}
    for row in records:
        if (row.company != company or row.party_type != "Supplier" or row.party != supplier
                or row.account not in accounts or row.account_type != "Payable" or row.delinked
                or getdate(row.posting_date) > cutoff
                or row.account_currency != (accounts[row.account].account_currency or currency)):
            _invalid()
        if row.voucher_type == "Purchase Order":
            _invalid()
        _money(row.amount)
        _money(row.amount_in_account_currency)
        for doctype, name in ((row.voucher_type, row.voucher_no),
                              (row.against_voucher_type, row.against_voucher_no)):
            _source(doctype, name, row.account, company, supplier, sources, set())
    return sources, records


def _completed_reposting(company, supplier, cutoff, accounts, sources):
    pairs = {(doctype, name) for doctype, name, _ in sources}
    # A submitted PI can temporarily have no PLE during an unfinished rebuild.
    # These internal candidate identities are only used to detect relevant jobs.
    pairs.update(("Purchase Invoice", row.name) for row in frappe.get_all("Purchase Invoice", filters={
        "company": company, "supplier": supplier, "credit_to": ["in", sorted(accounts)],
        "docstatus": 1, "posting_date": ["<=", str(cutoff)],
    }, fields=["name"], limit_page_length=0))
    # A PE/JE rebuild can likewise temporarily remove the settlement ledger.
    # Inspect only identities of matching native party/account sources, including
    # cancelled historical sources whose reversal may remain active.
    for payment_type, account_field in (("Pay", "paid_to"), ("Receive", "paid_from")):
        pairs.update(("Payment Entry", row.name) for row in frappe.get_all("Payment Entry", filters={
            "company": company, "party_type": "Supplier", "party": supplier, "payment_type": payment_type,
            account_field: ["in", sorted(accounts)], "docstatus": ["in", [1, 2]],
        }, fields=["name"], limit_page_length=0))
    journal_rows = frappe.get_all("Journal Entry Account", filters={"parenttype": "Journal Entry",
        "party_type": "Supplier", "party": supplier, "account": ["in", sorted(accounts)]},
        fields=["parent"], limit_page_length=0)
    journal_names = sorted({row.parent for row in journal_rows})
    if journal_names:
        pairs.update(("Journal Entry", row.name) for row in frappe.get_all("Journal Entry", filters={
            "name": ["in", journal_names], "company": company, "docstatus": ["in", [1, 2]],
        }, fields=["name"], limit_page_length=0))
    if not pairs:
        return (), ()
    types, names = sorted({pair[0] for pair in pairs}), sorted({pair[1] for pair in pairs})
    jobs = []
    for doctype, child, status_field, completed in (
        ("Repost Payment Ledger", "Repost Payment Ledger Items", "repost_status", {"Completed"}),
        ("Repost Accounting Ledger", "Repost Accounting Ledger Items", "status", {"Completed", "Cancelled"}),
    ):
        candidates = frappe.get_all(child, filters={"parenttype": doctype, "voucher_type": ["in", types],
            "voucher_no": ["in", names]}, fields=["parent", "voucher_type", "voucher_no"], limit_page_length=0)
        related = [row for row in candidates if (row.voucher_type, row.voucher_no) in pairs]
        parents = {row.parent for row in related}
        jobs.append((doctype, _records_state(related)))
        for parent in parents:
            state = frappe.db.get_value(doctype, parent, ["docstatus", status_field], as_dict=True)
            if not state:
                _invalid()
            if state.docstatus == 1 and state.get(status_field) not in completed:
                frappe.throw("相关应付凭证的账簿重过账尚未完成或已失败，请完成原生重过账后查询")
            jobs.append((doctype, parent, _freeze(state)))
    return tuple(sorted(pairs)), tuple(sorted(jobs, key=repr))


def _permission_state(documents):
    """Compare raw permission inputs too: native role/meta checks may be cached."""
    user = frappe.session.user
    doctypes = sorted({doctype for doctype, _ in documents} | {PLE, "Journal Entry Account"})

    def raw(doctype, filters, fields=None):
        return frappe.get_all(doctype, filters=filters, fields=fields or ["*"], limit_page_length=0)

    roles = raw("Has Role", {"parenttype": "User", "parent": user})
    report_roles = raw("Has Role", {"parenttype": "Report", "parent": REPORT})
    custom_roles = raw("Custom Role", {"report": REPORT})
    custom_role_rows = raw("Has Role", {"parenttype": "Custom Role", "parent": ["in", [r.name for r in custom_roles]]}) if custom_roles else []
    permissions = []
    for doctype, filters in (
        ("User Permission", {"user": user}),
        ("DocPerm", {"parent": ["in", doctypes]}),
        ("Custom DocPerm", {"parent": ["in", doctypes]}),
        ("DocType", {"name": ["in", doctypes]}),
        ("DocField", {"parent": ["in", doctypes]}),
        ("Custom Field", {"dt": ["in", doctypes]}),
        ("Property Setter", {"doc_type": ["in", doctypes]}),
        ("Report", {"name": REPORT}),
    ):
        permissions.append((doctype, _records_state(raw(doctype, filters))))
    shares = raw("DocShare", {"share_doctype": ["in", doctypes],
        "share_name": ["in", sorted({name for _, name in documents})]})
    shares = [row for row in shares if (row.share_doctype, row.share_name) in documents
              and (row.user == user or row.everyone)]
    # Only non-secret user attributes affecting roles are inspected internally.
    user_state = raw("User", {"name": user}, ["name", "enabled", "user_type"])
    return _freeze((user, user_state, _records_state(roles), _records_state(report_roles),
        _records_state(custom_roles), _records_state(custom_role_rows), permissions, _records_state(shares)))


def _capture(company, supplier, report_date, payable_account):
    from frappe.desk import query_report

    if frappe.conf.get("read_from_replica"):
        frappe.throw("当前应付查询需要主库口径，请在主库完成账簿核对后查询")
    cutoff, currency, accounts, masters = _scope(company, supplier, report_date, payable_account)
    query_report.get_report_doc(REPORT)
    sources, ledger = _ledger_closure(company, supplier, cutoff, currency, accounts)
    repost = _completed_reposting(company, supplier, cutoff, accounts, sources)
    documents = dict(masters)
    documents.update(((doctype, name), doc) for (doctype, name, _), doc in sources.items())
    for doc in sources.values():
        if doc.doctype == "Journal Entry" and doc.get("jf_expense_voucher"):
            from jifeng_erp.accounting import SOURCE, SOURCE_FIELDS as EXPENSE_FIELDS

            documents[SOURCE, doc.jf_expense_voucher] = _document(SOURCE, doc.jf_expense_voucher, EXPENSE_FIELDS)
    documents.update(((PLE, row.name), row) for row in ledger)
    state = _freeze((documents, _records_state(accounts.values()), _records_state(ledger), repost,
                     _permission_state(documents)))
    return cutoff, currency, accounts, sources, state


def _native_total(total, columns, currency):
    # Frappe.add_total_row appends one list. The standard AP first column is
    # Date, so its first cell is blank; supported label columns use _('Total').
    if len(total) != len(columns):
        _invalid()
    numeric = {"Currency", "Int", "Float", "Percent", "Duration"}
    unsupported_first = {"Currency", "Int", "Float", "Percent", "Date", "Datetime", "Time"}
    for index, (column, value) in enumerate(zip(columns, total, strict=True)):
        fieldtype = column.get("fieldtype")
        if not fieldtype:
            _invalid()
        if index == 0 and fieldtype not in unsupported_first:
            if value != frappe._("Total"):
                _invalid()
        elif fieldtype in numeric:
            if value != "":
                _money(value)
        elif fieldtype == "Link" and column.get("options") == "Currency":
            if value != currency:
                _invalid()
        elif value != "":
            _invalid()


def _report_rows(result, company, supplier, cutoff, currency, accounts, sources):
    if not isinstance(result, dict) or not isinstance(result.get("result"), list):
        _invalid()
    columns = result.get("columns") or []
    if not all(isinstance(column, dict) and column.get("fieldname") for column in columns):
        _invalid()
    fields = [column["fieldname"] for column in columns]
    if len(fields) != len(set(fields)) or not set(ROW_FIELDS).issubset(fields):
        _invalid()
    native_rows = result["result"]
    if native_rows and isinstance(native_rows[-1], list):
        total = native_rows[-1]
        if not result.get("add_total_row") or len(native_rows) < 2:
            _invalid()
        _native_total(total, columns, currency)
        native_rows = native_rows[:-1]
    rows = []
    for row in native_rows:
        if not isinstance(row, dict) or not REQUIRED_ROW_FIELDS.issubset(row):
            _invalid()
        account = row["party_account"]
        source = sources.get((row["voucher_type"], row["voucher_no"], account))
        if (row["party_type"] != "Supplier" or row["party"] != supplier or account not in accounts
                or row["currency"] != currency or not source or source.company != company):
            _invalid()
        if not row["posting_date"] or getdate(row["posting_date"]) > cutoff:
            _invalid()
        rows.append({"voucher_type": row["voucher_type"], "voucher_no": row["voucher_no"],
            "posting_date": str(getdate(row["posting_date"])),
            "due_date": str(getdate(row["due_date"])) if row.get("due_date") else None,
            "payable_account": account, "currency": currency,
            **{field: _money(row[field]) for field in MONEY_FIELDS}})
    return rows


@frappe.whitelist(methods=["GET"])
def query_payables(company, supplier, report_date, payable_account=None):
    """Native company-currency outstanding sources as of an explicit date.

    An optional account must use company currency because native AP switches to
    account currency when party_account is set. No account filter uses native
    base amounts, including for foreign-currency payable accounts.
    """
    from frappe.desk import query_report

    cutoff, currency, accounts, sources, before = _capture(company, supplier, report_date, payable_account)
    filters = {"company": company, "report_date": str(cutoff), "party_type": "Supplier", "party": [supplier],
        "in_party_currency": 0, "show_future_payments": 0, "group_by_party": 0, "ignore_accounts": 0,
        "based_on_payment_terms": 0, "for_revaluation_journals": 0, "handle_employee_advances": 0,
        "show_remarks": 0, "age_as_on": "Report Date", "ageing_based_on": "Due Date", "range": "30,60,90,120"}
    if payable_account:
        filters["party_account"] = payable_account
    try:
        result = query_report.run(REPORT, filters=filters, ignore_prepared_report=True, are_default_filters=False,
            js_filters=[{"fieldname": "company", "fieldtype": "Link", "options": "Company"},
                        {"fieldname": "party_account", "fieldtype": "Link", "options": "Account"}])
    except frappe.PermissionError:
        _deny()
    _, _, _, _, after = _capture(company, supplier, report_date, payable_account)
    if before != after:
        frappe.throw("查询期间应付来源或权限已变化，请重新查询")
    rows = _report_rows(result, company, supplier, cutoff, currency, accounts, sources)
    return {"company": company, "supplier": supplier, "report_date": str(cutoff), "currency": currency,
            "amount_basis": "company_currency", "payable_account": payable_account,
            "accounts": sorted(accounts), "rows": rows, "source_report": REPORT}
