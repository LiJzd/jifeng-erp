"""Read the exact wage earnings in a submitted native payroll accrual.

Approval and payment are not costs here. The first supported accounting shape
is company-currency payroll with an exact expense-group / effective-GL match.
The caller checks overlaps with inventory operating costs. No source is written.
"""

from collections import defaultdict
from decimal import Decimal, InvalidOperation

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import flt, getdate


FLOW, WAGE, AS, SLIP = "Jifeng Flow Order", "Jifeng Wage Entry", "Additional Salary", "Salary Slip"
PAYROLL, JE, GL = "Payroll Entry", "Journal Entry", "GL Entry"
IDENTITY = {"name", "modified", "docstatus"}
CHILD = IDENTITY | {"parent", "parenttype", "parentfield", "idx"}
FIELDS = {
    FLOW: IDENTITY | {"company", "item_code"},
    WAGE: IDENTITY | {"company", "flow_order", "employee", "salary_component", "payroll_date",
        "work_order", "job_card", "operation", "stock_uom", "quantity", "piece_amount", "time_amount", "extra_amount", "total_amount"},
    AS: IDENTITY | {"company", "employee", "currency", "salary_component", "payroll_date", "type",
        "amount", "ref_doctype", "ref_docname", "is_recurring", "disabled", "overwrite_salary_structure_amount"},
    SLIP: IDENTITY | {"company", "employee", "currency", "exchange_rate", "start_date", "end_date",
        "payroll_entry", "journal_entry", "salary_slip_based_on_timesheet"},
    PAYROLL: IDENTITY | {"company", "currency", "exchange_rate", "start_date", "end_date", "payroll_payable_account",
        "salary_slip_based_on_timesheet"},
    JE: IDENTITY | {"company", "voucher_type", "multi_currency", "total_debit", "total_credit"},
    GL: IDENTITY | {"company", "voucher_type", "voucher_no", "account", "account_currency", "debit", "credit", "is_cancelled"},
    "Work Order": IDENTITY | {"company", "production_item", "jf_flow_order"},
    "Job Card": IDENTITY | {"company", "work_order", "operation", "operation_id", "stock_uom", "is_subcontracted"},
    "Employee": {"name", "modified", "company"},
    "Salary Component": {"name", "modified", "type"},
    "Account": {"name", "modified", "company", "account_currency", "root_type", "is_group"},
    "Company": {"name", "modified", "default_currency"},
}
TABLES = {
    SLIP: (("earnings", "Salary Detail", CHILD | {"salary_component", "amount", "additional_salary",
        "do_not_include_in_total", "do_not_include_in_accounts", "is_recurring_additional_salary"}),
        ("deductions", "Salary Detail", CHILD | {"salary_component", "amount", "additional_salary",
        "do_not_include_in_total", "do_not_include_in_accounts", "is_recurring_additional_salary"})),
    "Salary Component": (("accounts", "Salary Component Account", CHILD | {"company", "account"}),),
    JE: (("accounts", "Journal Entry Account", CHILD | {"account", "account_currency", "exchange_rate",
        "debit", "credit", "reference_type", "reference_name"}),),
    "Work Order": (("operations", "Work Order Operation", CHILD | {"operation"}),),
}


def _deny():
    frappe.throw("无法完整读取流程计薪、工资单、计提凭证及其字段或权限，不能返回部分工资成本", frappe.PermissionError)


def _invalid():
    frappe.throw("流程工资的计薪、附加薪资、工资单与有效计提费用不一致；请核对原生工资来源")


def _freeze(value):
    if callable(getattr(value, "as_dict", None)):
        value = value.as_dict()
    if isinstance(value, dict):
        return tuple((key, _freeze(item)) for key, item in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return type(value).__name__, repr(value)


def _number(value):
    if isinstance(value, bool) or value in (None, ""):
        _invalid()
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        _invalid()
    if not number.is_finite() or number < 0:
        _invalid()
    return number


def _same(left, right, document, field):
    precision = document.precision(field)
    if type(precision) is not int or not 0 <= precision <= 9:
        _invalid()
    return flt(left, precision) == flt(right, precision)


def _fields(doctype, fields, parenttype=None):
    if not set(fields).issubset(get_permitted_fields(doctype, parenttype=parenttype,
            permission_type="read", ignore_virtual=True)):
        _deny()
    if frappe.session.user != "Administrator":
        meta = frappe.get_meta(doctype)
        masks = set(meta.get_permlevel_access(permission_type="mask", parenttype=parenttype, user=frappe.session.user))
        if any(field and field.get("mask") and (field.permlevel or 0) not in masks
                for field in (meta.get_field(name) for name in fields)):
            _deny()


def _authorization():
    user = frappe.session.user
    types = sorted(set(FIELDS) | {child for tables in TABLES.values() for _, child, _ in tables})
    return user, tuple((kind, _freeze(frappe.get_all(kind, filters=filters,
        fields=["name", "enabled", "user_type"] if kind == "User" else ["*"],
        order_by="name asc", limit_page_length=0))) for kind, filters in (
        ("User", {"name": user}), ("Has Role", {"parenttype": "User", "parent": user}),
        ("User Permission", {"user": user}), ("DocPerm", {"parent": ["in", types]}),
        ("Custom DocPerm", {"parent": ["in", types]}), ("DocType", {"name": ["in", types]}),
        ("DocField", {"parent": ["in", types]}), ("Custom Field", {"dt": ["in", types]}),
        ("Property Setter", {"doc_type": ["in", types]}), ("DocShare", {"share_doctype": ["in", types]})))


class _Read:
    def __init__(self):
        self.queries, self.docs, self.children = [], {}, {}
        self.authorization = _authorization()

    def records(self, kind, filters, fields, parent=None):
        raw = frappe.get_all(kind, filters=filters, fields=["*"], order_by="name asc", limit_page_length=0)
        if not raw:
            # No source means no financial disclosure. Retain the exact empty
            # discovery range so a later new source invalidates the result.
            self.queries.append((kind, filters, None, parent, _freeze(raw), None))
            return []
        _fields(kind, fields, parent)
        rows = frappe.get_list(kind, filters=filters, fields=sorted(fields), order_by="name asc",
            limit_page_length=0, **({"parent_doctype": parent} if parent else {}))
        names = [row.get("name") for row in rows]
        raw_names = [row.get("name") for row in raw]
        if (None in names + raw_names or len(set(names)) != len(names) or len(set(raw_names)) != len(raw_names)
                or set(names) != set(raw_names) or any(not set(fields).issubset(row) for row in rows)):
            _deny()
        actual = {row["name"]: row for row in raw}
        if any(any(actual[row["name"]].get(field) != row[field] for field in fields) for row in rows):
            _invalid()
        self.queries.append((kind, filters, fields, parent, _freeze(raw), _freeze(rows)))
        return rows

    def table_access(self, doc, field, kind, fields):
        meta = frappe.get_meta(doc.doctype)
        df = meta.get_field(field)
        if (not df or df.fieldtype != "Table" or df.options != kind
                or (df.permlevel or 0) not in meta.get_permlevel_access(permission_type="read", user=frappe.session.user)):
            _deny()
        if (frappe.session.user != "Administrator" and df.get("mask")
                and (df.permlevel or 0) not in meta.get_permlevel_access(permission_type="mask", user=frappe.session.user)):
            _deny()
        _fields(kind, fields, doc.doctype)

    def source(self, kind, name):
        if (kind, name) in self.docs:
            return self.docs[kind, name][0]
        if not isinstance(name, str) or not name or name != name.strip():
            _invalid()
        rows = self.records(kind, {"name": name}, FIELDS[kind])
        if len(rows) != 1:
            _deny()
        try:
            doc = frappe.get_doc(kind, name)
            doc.check_permission("read")
        except (frappe.PermissionError, frappe.DoesNotExistError):
            _deny()
        if any(doc.get(field) != rows[0][field] for field in FIELDS[kind]):
            _invalid()
        self.docs[kind, name] = doc, _freeze(doc)
        for field, childtype, fields in TABLES.get(kind, ()):
            self.table_access(doc, field, childtype, fields)
            children = self.records(childtype, {"parent": name, "parentfield": field}, fields, kind)
            native = {row.name: row for row in doc.get(field) or []}
            if len(native) != len(doc.get(field) or []) or set(native) != {row["name"] for row in children}:
                _deny()
            if any(row["parenttype"] != kind or any(native[row["name"]].get(key) != value
                    for key, value in row.items()) for row in children):
                _invalid()
            self.children[kind, name, field] = children
        return doc

    def table(self, doc, field):
        return self.children[doc.doctype, doc.name, field]

    def finish(self):
        # Reload every source before checking permissions; retain the full raw
        # source/query graph, not just a coincidentally unchanged monetary total.
        try:
            loaded = {key: frappe.get_doc(*key) for key in self.docs}
        except (frappe.PermissionError, frappe.DoesNotExistError):
            _deny()
        for (kind, _), doc in loaded.items():
            try:
                doc.check_permission("read")
            except frappe.PermissionError:
                _deny()
            _fields(kind, FIELDS[kind])
            for field, childtype, fields in TABLES.get(kind, ()):
                self.table_access(doc, field, childtype, fields)
            if _freeze(doc) != self.docs[kind, doc.name][1]:
                _deny()
        for kind, filters, fields, parent, raw, rows in self.queries:
            if fields is not None:
                _fields(kind, fields, parent)
                if _freeze(frappe.get_list(kind, filters=filters, fields=sorted(fields), order_by="name asc",
                        limit_page_length=0, **({"parent_doctype": parent} if parent else {}))) != rows:
                    _deny()
            if _freeze(frappe.get_all(kind, filters=filters, fields=["*"], order_by="name asc", limit_page_length=0)) != raw:
                _deny()
        # A later permission/list hook cannot edit an already-checked source.
        for key, (_, state) in self.docs.items():
            try:
                doc = frappe.get_doc(*key)
                doc.check_permission("read")
            except (frappe.PermissionError, frappe.DoesNotExistError):
                _deny()
            _fields(key[0], FIELDS[key[0]])
            if _freeze(doc) != state:
                _deny()
        if any(_freeze(frappe.get_all(kind, filters=filters, fields=["*"], order_by="name asc",
                limit_page_length=0)) != raw for kind, filters, _, _, raw, _ in self.queries):
            _deny()
        if _authorization() != self.authorization:
            _deny()


def _component_account(read, name, company, currency, earning):
    component = read.source("Salary Component", name)
    if component.type != ("Earning" if earning else "Deduction"):
        _invalid()
    mappings = [row for row in read.table(component, "accounts") if row["company"] == company]
    if len(mappings) != 1:
        _invalid()
    account = read.source("Account", mappings[0]["account"])
    if (account.company != company or account.account_currency != currency or account.is_group
            or (earning and account.root_type != "Expense")):
        _invalid()
    return account.name


def _accrual(read, entry, payroll, currency):
    if (entry.company != payroll.company or entry.voucher_type != "Journal Entry" or entry.multi_currency
            or payroll.currency != currency or _number(payroll.exchange_rate) != 1):
        _invalid()
    expected = defaultdict(lambda: [Decimal(0), Decimal(0)])
    slips = read.records(SLIP, {"journal_entry": entry.name, "docstatus": 1}, FIELDS[SLIP])
    if not slips:
        _invalid()
    for row in slips:
        slip = read.source(SLIP, row["name"])
        if (slip.payroll_entry != payroll.name or slip.company != payroll.company or slip.currency != currency
                or _number(slip.exchange_rate) != 1 or slip.salary_slip_based_on_timesheet != payroll.salary_slip_based_on_timesheet
                or not getdate(payroll.start_date) <= getdate(slip.start_date) <= getdate(slip.end_date) <= getdate(payroll.end_date)):
            _invalid()
        for field in ("earnings", "deductions"):
            for detail in read.table(slip, field):
                if detail["docstatus"] != 1:
                    _invalid()
                # This is the actual HRMS get_salary_components SQL condition.
                if detail["do_not_include_in_total"] == 0 or detail["do_not_include_in_accounts"] == 0:
                    account = _component_account(read, detail["salary_component"], payroll.company, currency, field == "earnings")
                    amount = _number(detail["amount"])
                    if amount:
                        expected[account][field == "deductions"] += amount
    payable = read.source("Account", payroll.payroll_payable_account)
    if payable.company != payroll.company or payable.account_currency != currency or payable.is_group or payable.root_type != "Liability":
        _invalid()
    expected[payable.name][1] += sum((debit - credit for debit, credit in expected.values()), Decimal(0))
    if any(value < 0 for amounts in expected.values() for value in amounts):
        _invalid()
    actual = defaultdict(lambda: [Decimal(0), Decimal(0)])
    linked = False
    for row in read.table(entry, "accounts"):
        account = read.source("Account", row["account"])
        if (account.company != payroll.company or account.account_currency != currency or account.is_group
                or row["account_currency"] != currency or _number(row["exchange_rate"]) != 1):
            _invalid()
        if row["reference_type"] or row["reference_name"]:
            if row["reference_type"] != PAYROLL or row["reference_name"] != payroll.name or row["account"] != payable.name:
                _invalid()
            linked = True
        actual[row["account"]][0] += _number(row["debit"])
        actual[row["account"]][1] += _number(row["credit"])
    if not linked or set(actual) != set(expected) or any(not _same(actual[key][side], expected[key][side], entry, "total_debit")
            for key in actual for side in (0, 1)):
        _invalid()
    if (not _same(sum((amounts[0] for amounts in actual.values()), Decimal(0)), _number(entry.total_debit), entry, "total_debit")
            or not _same(sum((amounts[1] for amounts in actual.values()), Decimal(0)), _number(entry.total_credit), entry, "total_credit")
            or not _same(entry.total_debit, entry.total_credit, entry, "total_debit")):
        _invalid()
    ledger = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for row in read.records(GL, {"voucher_type": JE, "voucher_no": entry.name, "is_cancelled": 0}, FIELDS[GL]):
        doc = read.source(GL, row["name"])
        if doc.company != payroll.company or doc.account_currency != currency:
            _invalid()
        ledger[doc.account][0] += _number(doc.debit)
        ledger[doc.account][1] += _number(doc.credit)
    # Native GL merges debit/credit lines for the same account. Match net by
    # account, while checking the full submitted JE's separate expense groups.
    if set(ledger) != set(actual) or any(not _same(ledger[key][0] - ledger[key][1], actual[key][0] - actual[key][1],
            entry, "total_debit") for key in actual):
        _invalid()


def posted_flow_wage_costs(flow_order):
    """Return rows/total/currency and an internal finish_guard for the caller.

    Invoke finish_guard after all other cost sources have been read and before
    returning a DTO. Do not serialize that callable to a client.
    """
    if frappe.session.user == "Guest":
        _deny()
    if frappe.conf.get("read_from_replica"):
        frappe.throw("流程工资成本需要主库口径，请在主库核对后查询")
    read = _Read()
    flow = read.source(FLOW, flow_order)
    company = read.source("Company", flow.company)
    currency, output, checked = company.default_currency, [], set()
    if not currency:
        _invalid()
    for row in read.records(WAGE, {"flow_order": flow.name, "docstatus": 1}, FIELDS[WAGE]):
        wage = read.source(WAGE, row["name"])
        if wage.company != flow.company:
            _invalid()
        additions = read.records(AS, {"ref_doctype": WAGE, "ref_docname": wage.name, "docstatus": ["in", [0, 1]]}, FIELDS[AS])
        if len(additions) > 1:
            _invalid()
        if not additions or additions[0]["docstatus"] != 1:
            continue
        addition = read.source(AS, additions[0]["name"])
        if (addition.disabled or addition.is_recurring or addition.overwrite_salary_structure_amount or addition.type != "Earning"
                or addition.currency != currency or any(addition.get(key) != wage.get(key)
                    for key in ("company", "employee", "salary_component", "payroll_date"))
                or _number(wage.total_amount) <= 0
                or not _same(_number(addition.amount), _number(wage.total_amount), addition, "amount")):
            _invalid()
        details = read.records("Salary Detail", {"additional_salary": addition.name, "parenttype": SLIP,
            "docstatus": ["in", [0, 1]]}, TABLES[SLIP][0][2], SLIP)
        if len(details) > 1:
            _invalid()
        if not details:
            continue
        detail = details[0]
        slip = read.source(SLIP, detail["parent"])
        if slip.docstatus != 1 or not slip.payroll_entry or not slip.journal_entry:
            continue
        payroll, entry = read.source(PAYROLL, slip.payroll_entry), read.source(JE, slip.journal_entry)
        if payroll.docstatus != 1 or entry.docstatus != 1:
            continue
        native_detail = next((item for item in slip.earnings if item.name == detail["name"]), None)
        if (not native_detail or detail["docstatus"] != 1 or detail["parentfield"] != "earnings"
                or detail["salary_component"] != wage.salary_component or detail["is_recurring_additional_salary"]
                or (detail["do_not_include_in_total"] != 0 and detail["do_not_include_in_accounts"] != 0)
                or not _same(_number(detail["amount"]), _number(addition.amount), native_detail, "amount")
                or not getdate(slip.start_date) <= getdate(wage.payroll_date) <= getdate(slip.end_date)
                or slip.company != wage.company or slip.employee != wage.employee):
            _invalid()
        employee = read.source("Employee", wage.employee)
        work = read.source("Work Order", wage.work_order) if wage.work_order else None
        card = read.source("Job Card", wage.job_card) if wage.job_card else None
        if (employee.company != flow.company
                or not _same(sum((_number(wage.get(key)) for key in ("piece_amount", "time_amount", "extra_amount")), Decimal(0)),
                    _number(wage.total_amount), wage, "total_amount")):
            _invalid()
        # Existing wage entry supports explicit manual flow attribution. It
        # does not become a Job Card completion or an inventory quantity.
        if work and (work.company != flow.company or work.jf_flow_order != flow.name or work.production_item != flow.item_code):
            _invalid()
        if card and (not work or card.company != flow.company or card.work_order != work.name or card.docstatus != 1
                or card.operation != wage.operation or card.is_subcontracted
                or len([op for op in read.table(work, "operations") if op["name"] == card.operation_id and op["operation"] == wage.operation]) != 1
                or (_number(wage.quantity) and wage.stock_uom != card.stock_uom)):
            _invalid()
        if entry.name not in checked:
            _accrual(read, entry, payroll, currency)
            checked.add(entry.name)
        output.append({"wage_entry": wage.name, "additional_salary": addition.name, "salary_slip": slip.name,
            "payroll_entry": payroll.name, "journal_entry": entry.name, "work_order": work.name if work else None, "job_card": card.name if card else None,
            "amount": _number(detail["amount"])})
    read.finish()
    result = {"rows": output, "total": sum((row["amount"] for row in output), Decimal(0)), "currency": currency}
    payload = _freeze(result)

    def finish_guard():
        read.finish()
        if _freeze({key: result[key] for key in ("rows", "total", "currency")}) != payload:
            _deny()

    result["finish_guard"] = finish_guard
    return result
