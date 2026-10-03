"""Read saved payroll settlements without recomputing or implying payment.

Only a complete, permission-checked side which reconciles to the saved native
total gets classified amounts. Missing classifications or wage provenance keep
that entire side unknown. GETs never lock, save, export or recalculate a slip.
"""

from datetime import date, datetime
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json

import frappe
from frappe.model import get_permitted_fields

from jifeng_erp.archive_versions import same_version
from jifeng_erp import salary_history_classification as rules


SCOPE = "salary_history"
BATCH_SIZE = 200
PAGE_SIZES = {15, 25, 50, 100}
CONTEXT_FIELDS = {"company", "from_date", "to_date", "use_date", "date_basis", "search"}
DATE_BASES = {"period_start", "period_end", "period_overlap", "posting_date"}
SLIP = "Salary Slip"
DETAIL = "Salary Detail"
SLIP_FIELDS = {
    "name", "modified", "docstatus", "company", "employee", "employee_name",
    "department", "currency", "exchange_rate", "start_date", "end_date",
    "posting_date", "total_working_days", "payment_days", "gross_pay",
    "total_deduction", "net_pay", "rounded_total",
}
DETAIL_FIELDS = {
    "name", "parent", "parenttype", "parentfield", "idx", "salary_component",
    "amount", "default_amount", "additional_amount", "additional_salary",
    "do_not_include_in_total", "statistical_component", "depends_on_payment_days",
}
AS_FIELDS = {
    "name", "modified", "docstatus", "company", "employee", "currency",
    "salary_component", "payroll_date", "ref_doctype", "ref_docname", "amount",
    "is_recurring", "overwrite_salary_structure_amount",
}
WAGE_FIELDS = {
    "name", "modified", "docstatus", "company", "employee", "salary_component",
    "payroll_date", "piece_amount", "time_amount", "extra_amount", "total_amount",
}
MONEY_FIELDS = {
    "gross_pay", "total_deduction", "net_pay", "rounded_total", "amount",
    "default_amount", "additional_amount", "piece_amount", "time_amount",
    "extra_amount", "total_amount", "exchange_rate", "total_working_days", "payment_days",
}
DAY_FIELDS = {"start_date", "end_date", "posting_date", "payroll_date"}
UNKNOWN_FIELDS = ("description", "type", "actual_attendance", "core_total", "actual_paid")


class _Unavailable(Exception):
    pass


class _Changed(Exception):
    pass


class _Unclassified(Exception):
    pass


def _deny():
    frappe.throw("没有完整读取本次工资历史及所需字段的权限或有效来源", frappe.PermissionError)


def _name(value):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 140:
        raise ValueError
    return value


def _day(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str) or len(value) != 10 or date.fromisoformat(value).isoformat() != value:
        raise ValueError
    return value


def _money(value):
    if isinstance(value, bool) or value is None or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError
    text = str(value)
    if not text or len(text) > 100 or text != text.strip():
        raise ValueError
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise ValueError from error
    if not number.is_finite() or abs(number.as_tuple().exponent) > 308 or number.adjusted() > 308:
        raise ValueError
    return "0" if not number else format(number, "f")


def _normal(field, value):
    if field == "modified":
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.isoformat()
        if not isinstance(value, str) or not same_version(value, value):
            raise ValueError
        return datetime.fromisoformat(value).isoformat()
    if field in DAY_FIELDS:
        return _day(value)
    if field in {"default_amount", "additional_amount"} and value is None:
        return None
    if field in MONEY_FIELDS:
        return _money(value)
    if field in {"is_recurring", "overwrite_salary_structure_amount", "do_not_include_in_total", "statistical_component", "depends_on_payment_days"}:
        if value not in (None, 0, 1, False, True) or isinstance(value, str):
            raise ValueError
        return int(value or 0)
    if field == "docstatus":
        # Native BaseDocument promotes this to DocStatus, an int subclass.
        if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1, 2):
            raise ValueError
        return int(value)
    if field in {"additional_salary", "ref_doctype", "ref_docname", "department"}:
        return value or ""
    return value


def _context(value):
    try:
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, dict) or set(value) != CONTEXT_FIELDS:
            raise ValueError
        company = _name(value["company"])
        start, end = _day(value["from_date"]), _day(value["to_date"])
        search = value["search"]
        if (type(value["use_date"]) is not bool or value["date_basis"] not in DATE_BASES
                or start > end or not isinstance(search, str) or len(search) > 140
                or any(ord(character) < 32 for character in search)):
            raise ValueError
        return {**value, "company": company, "from_date": start, "to_date": end, "search": search.strip()}
    except (ValueError, TypeError, OverflowError):
        frappe.throw("工资历史查询参数无效，请核对公司、日期范围、日期口径及关键词")


def _columns(doctype, permission="read", parenttype=None):
    columns = set(get_permitted_fields(doctype, parenttype=parenttype,
        permission_type=permission, ignore_virtual=True))
    if frappe.session.user != "Administrator":
        meta = frappe.get_meta(doctype)
        levels = set(meta.get_permlevel_access(permission_type="mask", parenttype=parenttype,
            user=frappe.session.user))
        columns = {name for name in columns if not ((field := meta.get_field(name))
            and field.get("mask") and (field.permlevel or 0) not in levels)}
    return columns


def _fields(doctype, fields, parenttype=None):
    if not set(fields).issubset(_columns(doctype, parenttype=parenttype)):
        raise _Unavailable


def _access(document, *permissions):
    if not all(frappe.has_permission(document.doctype, permission, doc=document, throw=False)
            for permission in permissions):
        raise _Unavailable


def _meta_contract(doctype, expected):
    meta = frappe.get_meta(doctype)
    for name, (kind, options) in expected.items():
        field = meta.get_field(name)
        if not field or field.fieldtype != kind or (options and field.options != options):
            raise _Unavailable


def _permission_state():
    """Raw, internal permission inputs catch changes hidden by native caches."""
    doctypes = [SLIP, DETAIL, "Company", "Employee", "Currency", "Salary Component",
        "Additional Salary", "Jifeng Wage Entry", "Jifeng Salary History Rule"]
    user = frappe.session.user
    items = [("actor", user)]
    queries = [
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
    ]
    for doctype, filters, fields in queries:
        records = frappe.get_all(doctype, filters=filters, fields=fields, limit_page_length=0)
        values = [json.dumps(dict(row), sort_keys=True, default=str, ensure_ascii=False) for row in records]
        items.append((doctype, sorted(values)))
    return hashlib.sha256(json.dumps(items, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class _Capture:
    def __init__(self):
        self.records = {}

    def project(self, doctype, name, fields, *, select=False):
        try:
            document = frappe.get_doc(doctype, _name(name))
        except (frappe.PermissionError, frappe.DoesNotExistError) as error:
            raise _Unavailable from error
        _access(document, "read", *(["select"] if select else []))
        _fields(doctype, fields)
        rows = frappe.get_list(doctype, filters={"name": name}, fields=sorted(fields), limit_page_length=1)
        if len(rows) != 1 or not set(fields).issubset(rows[0]):
            raise _Unavailable
        try:
            projected = {field: _normal(field, rows[0][field]) for field in fields}
            actual = {field: _normal(field, document.get(field)) for field in fields}
        except (ValueError, TypeError, OverflowError) as error:
            raise _Unavailable from error
        if projected != actual:
            raise _Changed
        key = doctype, name
        if key in self.records and self.records[key] != projected:
            raise _Changed
        self.records[key] = projected
        _access(document, "read", *(["select"] if select else []))
        _fields(doctype, fields)
        return document, projected


def _table_field(document, table):
    # Tables are NO_VALUE_FIELDS and never appear in get_permitted_fields.
    # Guard their own read/mask levels directly; child value fields still use
    # the native permitted-field projection with parenttype below.
    _access(document, "read")
    _meta_contract(SLIP, {table: ("Table", DETAIL)})
    field = document.meta.get_field(table)
    levels = set(document.meta.get_permlevel_access(permission_type="read", user=frappe.session.user))
    # Match native Meta.get_permitted_fieldnames's shared-document level-zero
    # fallback. The specific parent's native read permission is checked above.
    if 0 not in levels and frappe.share.get_shared(SLIP, frappe.session.user, rights=["read"], limit=1):
        levels.add(0)
    if (field.permlevel or 0) not in levels:
        raise _Unavailable
    if frappe.session.user != "Administrator" and field.get("mask"):
        mask_levels = set(document.meta.get_permlevel_access(permission_type="mask", user=frappe.session.user))
        if (field.permlevel or 0) not in mask_levels:
            raise _Unavailable


def _children(document, table):
    _table_field(document, table)
    _fields(DETAIL, DETAIL_FIELDS, SLIP)
    _meta_contract(DETAIL, {"salary_component": ("Link", "Salary Component"),
        "additional_salary": ("Link", "Additional Salary"), "amount": ("Currency", None),
        "default_amount": ("Currency", None), "additional_amount": ("Currency", None)})
    rows, seen = [], set()
    while True:
        batch = frappe.get_list(DETAIL, parent_doctype=SLIP, filters={"parent": document.name,
            "parenttype": SLIP, "parentfield": table}, fields=sorted(DETAIL_FIELDS),
            order_by="idx asc, name asc", limit_start=len(rows), limit_page_length=BATCH_SIZE)
        for record in batch:
            if not DETAIL_FIELDS.issubset(record) or record["name"] in seen:
                raise _Unavailable
            seen.add(record["name"])
            try:
                rows.append({field: _normal(field, record[field]) for field in DETAIL_FIELDS})
            except (ValueError, TypeError, OverflowError) as error:
                raise _Unavailable from error
        if len(batch) < BATCH_SIZE:
            break
    try:
        actual = [{field: _normal(field, child.get(field)) for field in DETAIL_FIELDS}
            for child in document.get(table) or []]
        actual.sort(key=lambda row: (row["idx"], row["name"]))
    except (ValueError, TypeError, OverflowError) as error:
        raise _Unavailable from error
    if actual != rows:
        raise _Unavailable
    _table_field(document, table)
    _fields(DETAIL, DETAIL_FIELDS, SLIP)
    return rows


def _matches(row, context):
    if row["company"] != context["company"] or row["docstatus"] != 1:
        return False
    if context["use_date"]:
        start, end = context["from_date"], context["to_date"]
        basis = context["date_basis"]
        if basis == "period_overlap":
            if row["start_date"] > end or row["end_date"] < start:
                return False
        else:
            field = {"period_start": "start_date", "period_end": "end_date", "posting_date": "posting_date"}[basis]
            if not start <= row[field] <= end:
                return False
    search = context["search"].casefold()
    return not search or any(search in str(row[field] or "").casefold()
        for field in ("name", "employee", "employee_name", "department"))


def _source(capture, slip, detail):
    if not detail["additional_salary"]:
        return None, []
    _meta_contract("Additional Salary", {"company": ("Link", "Company"), "employee": ("Link", "Employee"),
        "currency": ("Link", "Currency"), "salary_component": ("Link", "Salary Component"),
        "amount": ("Currency", None), "ref_doctype": ("Link", "DocType"), "ref_docname": ("Dynamic Link", "ref_doctype")})
    _, additional = capture.project("Additional Salary", detail["additional_salary"], AS_FIELDS)
    if (additional["docstatus"] != 1 or any(additional[field] != slip[field]
            for field in ("company", "employee", "currency"))
            or additional["salary_component"] != detail["salary_component"]):
        raise _Unclassified
    references = [{"doctype": "Additional Salary", "name": additional["name"], "version": additional["modified"]}]
    if additional["ref_doctype"] != "Jifeng Wage Entry":
        return None, references
    _meta_contract("Jifeng Wage Entry", {"company": ("Link", "Company"), "employee": ("Link", "Employee"),
        "salary_component": ("Link", "Salary Component"), **{field: ("Currency", None)
            for field in ("piece_amount", "time_amount", "extra_amount", "total_amount")}})
    _, wage = capture.project("Jifeng Wage Entry", additional["ref_docname"], WAGE_FIELDS)
    _, company = capture.project("Company", slip["company"], {"name", "default_currency"}, select=True)
    if (wage["docstatus"] != 1 or additional["is_recurring"] or additional["overwrite_salary_structure_amount"]
            or any(wage[field] != additional[field] for field in ("company", "employee", "salary_component", "payroll_date"))
            or slip["currency"] != company["default_currency"]
            or not slip["start_date"] <= wage["payroll_date"] <= slip["end_date"]
            or Decimal(detail["amount"]) != Decimal(additional["amount"])
            or Decimal(wage["total_amount"]) != Decimal(additional["amount"])):
        raise _Unclassified
    parts = {"piece": Decimal(wage["piece_amount"]), "time": Decimal(wage["time_amount"]), "extra": Decimal(wage["extra_amount"])}
    if any(amount < 0 for amount in parts.values()) or sum(parts.values()) != Decimal(wage["total_amount"]):
        raise _Unclassified
    references.append({"doctype": "Jifeng Wage Entry", "name": wage["name"], "version": wage["modified"]})
    return parts, references


def _classify(capture, slip, children, table, available_rules, document):
    keys = rules.EARNING_BUCKETS if table == "earnings" else rules.DEDUCTION_BUCKETS
    amounts = {key: Decimal(0) for key in keys}
    original = Decimal(0)
    basic_default = Decimal(0)
    basic_default_known = True
    sources, details, verified = [], [], available_rules is not None
    for child in children:
        _, component = capture.project("Salary Component", child["salary_component"], {"name", "modified", "type"}, select=True)
        packed = {field: child[field] for field in ("name", "idx", "salary_component", "amount", "default_amount",
            "do_not_include_in_total", "statistical_component", "depends_on_payment_days")}
        packed.update(classification=None, sources=[])
        try:
            if component["type"] != ("Earning" if table == "earnings" else "Deduction"):
                raise _Unclassified
            parts, references = _source(capture, slip, child)
            rule = rules.resolve_rule(available_rules or [], child["salary_component"], table, slip["end_date"])
            if not rule:
                raise _Unclassified
            mode = rule.get("classification_mode") or "component"
            if (rule.get("bucket") not in keys or mode not in {"component", "wage_breakdown"}
                    or bool(parts is not None) != (mode == "wage_breakdown")):
                raise _Unclassified
            extra_bucket = rule.get("extra_bucket")
            if parts is not None and (table != "earnings" or extra_bucket not in keys):
                raise _Unclassified
            packed["classification"] = {"rule": rule["name"], "version": str(rule["modified"]),
                "bucket": rule["bucket"], "mode": mode, "effective_from": str(rule["effective_from"])}
            packed["sources"] = references
            sources.extend(references)
            if not child["do_not_include_in_total"]:
                amount = Decimal(child["amount"])
                original += amount
                if parts is None:
                    amounts[rule["bucket"]] += amount
                    if rule["bucket"] == "basic":
                        if child["default_amount"] is None:
                            basic_default_known = False
                        else:
                            basic_default += Decimal(child["default_amount"])
                else:
                    amounts["piece"] += parts["piece"]
                    amounts["time"] += parts["time"]
                    amounts[extra_bucket] += parts["extra"]
        except (_Unavailable, _Unclassified, frappe.PermissionError, frappe.DoesNotExistError):
            verified = False
            packed.update(classification=None, sources=[])
        details.append(packed)
    total_field = "gross_pay" if table == "earnings" else "total_deduction"
    precision = document.precision(total_field)
    if type(precision) is not int or not 0 <= precision <= 9:
        raise _Unavailable
    quantum = Decimal(1).scaleb(-precision)
    reconciled = original.quantize(quantum) == Decimal(slip[total_field]).quantize(quantum)
    state = "verified" if verified and reconciled else "unclassified" if not verified else "unreconciled"
    return {
        "state": state,
        "buckets": {key: _money(amount) if state == "verified" else None for key, amount in amounts.items()},
        "basic_default_amount": _money(basic_default) if state == "verified" and table == "earnings" and basic_default_known else None,
        "details": details, "sources": sources,
    }


def _unique_sources(children, sides):
    """One saved slip cannot consume an additional or wage source twice.

    Native totals can reconcile even when a source is duplicated. This separate
    identity check deliberately runs across both tables and before pagination.
    """
    additions = Counter(child["additional_salary"] for table in children
        for child in children[table] if child["additional_salary"])
    references = Counter((source["doctype"], source["name"]) for table in sides
        for item in sides[table]["details"] for source in item["sources"])
    repeated_additions = {name for name, count in additions.items() if count > 1}
    repeated_references = {identity for identity, count in references.items() if count > 1}
    for table, side in sides.items():
        invalid = False
        for child, packed in zip(children[table], side["details"]):
            if (child["additional_salary"] in repeated_additions
                    or any((source["doctype"], source["name"]) in repeated_references
                        for source in packed["sources"])):
                invalid = True
                packed.update(classification=None, sources=[])
        if invalid:
            side["state"] = "unclassified"
            side["buckets"] = {key: None for key in side["buckets"]}
            side["basic_default_amount"] = None
            side["sources"] = [source for packed in side["details"] for source in packed["sources"]]


def _collect(context):
    capture = _Capture()
    capture.project("Company", context["company"], {"name", "default_currency"}, select=True)
    _fields(SLIP, SLIP_FIELDS)
    _meta_contract(SLIP, {"company": ("Link", "Company"), "employee": ("Link", "Employee"),
        "currency": ("Link", "Currency"), "gross_pay": ("Currency", None),
        "total_deduction": ("Currency", None), "net_pay": ("Currency", None), "rounded_total": ("Currency", None)})
    if not frappe.has_permission(SLIP, "read", throw=False):
        raise _Unavailable
    try:
        available_rules = rules.load_rules(context["company"])
    except (frappe.PermissionError, frappe.DoesNotExistError):
        available_rules = None
    output, seen, scanned = [], set(), 0
    while True:
        batch = frappe.get_list(SLIP, filters={"company": context["company"], "docstatus": 1},
            fields=sorted(SLIP_FIELDS), order_by="end_date desc, name asc",
            limit_start=scanned, limit_page_length=BATCH_SIZE)
        for listed in batch:
            if not SLIP_FIELDS.issubset(listed) or listed["name"] in seen:
                raise _Changed
            seen.add(listed["name"])
            document, slip = capture.project(SLIP, listed["name"], SLIP_FIELDS)
            if slip != {field: _normal(field, listed[field]) for field in SLIP_FIELDS}:
                raise _Changed
            if slip["company"] != context["company"] or slip["docstatus"] != 1 or slip["start_date"] > slip["end_date"]:
                raise _Changed
            if not _matches(slip, context):
                continue
            capture.project("Employee", slip["employee"], {"name"}, select=True)
            capture.project("Currency", slip["currency"], {"name"}, select=True)
            children = {table: _children(document, table) for table in ("earnings", "deductions")}
            sides = {table: _classify(capture, slip, children[table], table, available_rules, document)
                for table in children}
            _unique_sources(children, sides)
            row = {field: slip[field] for field in SLIP_FIELDS - {"modified", "docstatus", "exchange_rate"}}
            row.update(version=slip["modified"], **{field: None for field in UNKNOWN_FIELDS},
                buckets={**sides["earnings"]["buckets"], **sides["deductions"]["buckets"]},
                basic_default_amount=sides["earnings"]["basic_default_amount"],
                classification_state={table: sides[table]["state"] for table in sides},
                unresolved=["旧说明、类型、实际出勤、核心合计及实发定义尚未核实；净应发不表示已付款"])
            for table, label in (("earnings", "收入"), ("deductions", "扣款")):
                if sides[table]["state"] != "verified":
                    row["unresolved"].append(label + "分类或准确来源未完整核实，分类金额全部留空")
            references = {tuple(reference[key] for key in ("doctype", "name", "version")): reference
                for table in sides for reference in sides[table]["sources"]}
            output.append({"row": row, "earnings": sides["earnings"]["details"],
                "deductions": sides["deductions"]["details"], "sources": list(references.values()),
                "children": children})
        scanned += len(batch)
        if len(batch) < BATCH_SIZE:
            break
    if available_rules is not None:
        rules.guard_rule_snapshot(available_rules, context["company"])
    return {"rows": output, "records": capture.records, "rules": available_rules}


def _stable(context):
    if frappe.session.user == "Guest":
        _deny()
    try:
        before = _permission_state()
        first = _collect(context)
        if before != _permission_state():
            raise _Changed
        second = _collect(context)
        if first != second or before != _permission_state():
            raise _Changed
        return second["rows"]
    except (_Unavailable, frappe.PermissionError, frappe.DoesNotExistError,
            ValueError, TypeError, InvalidOperation, OverflowError):
        _deny()
    except _Changed:
        frappe.throw("工资条、分类规则、来源或权限已改变，请重新查询并核对")


@frappe.whitelist(methods=["GET"])
def query_salary_history(context, page=1, page_size=15):
    context = _context(context)
    # Native GET form parameters arrive as strings; JSON booleans/floats are
    # not integers. Only canonical decimal strings or actual ints are accepted.
    if isinstance(page, str) and page.isascii() and page.isdigit() and str(int(page)) == page:
        page = int(page)
    if isinstance(page_size, str) and page_size.isascii() and page_size.isdigit() and str(int(page_size)) == page_size:
        page_size = int(page_size)
    if type(page) is not int or not 1 <= page <= 9007199254740991 or type(page_size) is not int or page_size not in PAGE_SIZES:
        frappe.throw("工资历史页码或每页数量无效")
    rows = _stable(context)
    start = (page - 1) * page_size
    return {"readonly": True, "scope": SCOPE, "context": context, "page": page, "page_size": page_size,
        "total": len(rows), "total_pages": (len(rows) + page_size - 1) // page_size,
        "rows": [item["row"] for item in rows[start:start + page_size]]}


@frappe.whitelist(methods=["GET"])
def get_salary_history_detail(context, name, version):
    context = _context(context)
    try:
        name = _name(name)
    except ValueError:
        frappe.throw("工资历史来源编号无效")
    rows = _stable(context)
    selected = [item for item in rows if item["row"]["name"] == name]
    if len(selected) != 1:
        _deny()
    item = selected[0]
    if not same_version(item["row"]["version"], version):
        frappe.throw("工资历史已改变，请重新查询后查看")
    return {"readonly": True, "scope": SCOPE, "context": context,
        **{key: item[key] for key in ("row", "earnings", "deductions", "sources")}}
