"""Read-only charts based on saved, submitted payroll settlements."""

from decimal import Decimal, localcontext
import hashlib
import json

import frappe

from jifeng_erp import salary_history as H
SCOPE = "salary_analysis"
OPERATION_FIELDS = H.WAGE_FIELDS | {"operation"}


def _deny():
    frappe.throw("没有完整读取本次工资分析来源的权限或有效来源", frappe.PermissionError)


def _employee(value):
    if value in (None, ""):
        return ""
    try:
        return H._name(value)
    except ValueError:
        frappe.throw("员工筛选无效")


def _context(value):
    try:
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, dict) or set(value) != H.CONTEXT_FIELDS | {"employee", "currency"}:
            raise ValueError
        result = {key: value[key] for key in H.CONTEXT_FIELDS}
        employee = _employee(value["employee"])
        currency = _employee(value["currency"])
        return H._context(result), employee, currency
    except (ValueError, TypeError, OverflowError):
        frappe.throw("工资分析筛选无效，请核对公司、日期及员工")


def _money(value):
    return H._money(value)


def _compare_snapshot(snapshot, capture):
    for identity, projected in capture.records.items():
        saved = snapshot["records"].get(identity)
        if saved is None or any(field in saved and saved[field] != value
                for field, value in projected.items()):
            raise H._Changed


def _operation(capture, snapshot, slip, child):
    """Return an exact additional-salary increment only when its saved row reconciles."""
    if not child["additional_salary"]:
        return None
    # The native independent Additional Salary row has amount == the wage
    # amount. Reuse the history reader's complete identity/period/amount check
    # for this layout; its separate default/extra fields are not a mix formula.
    try:
        parts, references = H._source(capture, slip, child)
    except H._Unclassified:
        parts, references = None, []
    if parts is not None:
        wages = [reference for reference in references if reference["doctype"] == "Jifeng Wage Entry"]
        if len(wages) != 1:
            raise H._Unclassified
        reference = wages[0]
        H._meta_contract("Jifeng Wage Entry", {"operation": ("Data", None)})
        operation_capture = H._Capture()
        _, wage = operation_capture.project("Jifeng Wage Entry", reference["name"], OPERATION_FIELDS)
        if wage["modified"] != reference["version"] or wage["docstatus"] != 1:
            raise H._Changed
        _compare_snapshot(snapshot, capture)
        _compare_snapshot(snapshot, operation_capture)
        if not isinstance(wage["operation"], str) or not wage["operation"].strip():
            return None
        return wage, Decimal(wage["total_amount"]), Decimal(0)
    H._meta_contract("Additional Salary", {"company": ("Link", "Company"), "employee": ("Link", "Employee"),
        "currency": ("Link", "Currency"), "salary_component": ("Link", "Salary Component"),
        "amount": ("Currency", None), "ref_doctype": ("Link", "DocType"),
        "ref_docname": ("Dynamic Link", "ref_doctype")})
    _, additional = capture.project("Additional Salary", child["additional_salary"], H.AS_FIELDS)
    if (additional["docstatus"] != 1 or additional["ref_doctype"] != "Jifeng Wage Entry"
            or additional["is_recurring"] or additional["overwrite_salary_structure_amount"]
            or any(additional[field] != slip[field] for field in ("company", "employee", "currency"))
            or additional["salary_component"] != child["salary_component"]
            or child["additional_amount"] is None
            or Decimal(additional["amount"]) != Decimal(child["additional_amount"])):
        return None
    H._meta_contract("Jifeng Wage Entry", {"operation": ("Data", None)})
    H._meta_contract("Jifeng Wage Entry", {"company": ("Link", "Company"), "employee": ("Link", "Employee"),
        "salary_component": ("Link", "Salary Component"), **{field: ("Currency", None)
            for field in ("piece_amount", "time_amount", "extra_amount", "total_amount")}})
    operation_capture = H._Capture()
    _, wage = operation_capture.project("Jifeng Wage Entry", additional["ref_docname"], OPERATION_FIELDS)
    _, company = capture.project("Company", slip["company"], {"name", "default_currency"}, select=True)
    if (wage["docstatus"] != 1
            or any(wage[field] != additional[field] for field in ("company", "employee", "salary_component", "payroll_date"))
            or slip["currency"] != company["default_currency"]
            or not slip["start_date"] <= wage["payroll_date"] <= slip["end_date"]
            or Decimal(wage["total_amount"]) != Decimal(additional["amount"])):
        return None
    parts = [Decimal(wage[field]) for field in ("piece_amount", "time_amount", "extra_amount")]
    if any(amount < 0 for amount in parts) or sum(parts) != Decimal(wage["total_amount"]):
        return None
    base = child["default_amount"]
    if base is None or Decimal(base) + Decimal(child["additional_amount"]) != Decimal(child["amount"]):
        return None
    _compare_snapshot(snapshot, capture)
    _compare_snapshot(snapshot, operation_capture)
    if not isinstance(wage["operation"], str) or not wage["operation"].strip():
        return None
    return wage, Decimal(child["additional_amount"]), Decimal(base)


def _build(snapshot, employee_filter, currency_filter):
    totals = {key: Decimal(0) for key in ("gross_pay", "total_deduction", "net_pay", "rounded_total")}
    departments, groups, operations, sources, slips = {}, {}, {}, [], []
    capture = H._Capture()
    seen_wages = set()
    selected = 0
    for item in snapshot["rows"]:
        slip = item["row"]
        if employee_filter and slip["employee"] != employee_filter:
            continue
        if currency_filter and slip["currency"] != currency_filter:
            continue
        selected += 1
        slips.append({field: slip[field] for field in ("name", "version", "employee", "employee_name",
            "department", "currency", "start_date", "end_date", "gross_pay", "total_deduction",
            "net_pay", "rounded_total")})
        for key in totals:
            totals[key] += Decimal(slip[key])
        department = slip["department"] or "未归类"
        departments[department] = departments.get(department, Decimal(0)) + Decimal(slip["gross_pay"])
        # No immutable historical employee group is captured on Salary Slip.
        groups["未归类"] = groups.get("未归类", Decimal(0)) + Decimal(slip["gross_pay"])
        for child in item["children"]["earnings"]:
            if child["do_not_include_in_total"]:
                continue
            try:
                wage = _operation(capture, snapshot, slip, child)
            except (H._Unavailable, H._Unclassified, frappe.PermissionError, frappe.DoesNotExistError):
                # Keep all saved earnings visible in settlement totals. A source
                # that cannot be proved belongs to the operation chart's unknown bucket.
                operations["未归类"] = operations.get("未归类", Decimal(0)) + Decimal(child["amount"])
                continue
            if wage is None:
                operations["未归类"] = operations.get("未归类", Decimal(0)) + Decimal(child["amount"])
                continue
            wage, wage_amount, base_amount = wage
            if wage["name"] in seen_wages:
                raise H._Changed
            seen_wages.add(wage["name"])
            source_amount = Decimal(wage["total_amount"])
            additional = snapshot["records"].get(("Additional Salary", child["additional_salary"]))
            if not additional or not isinstance(additional.get("modified"), str):
                raise H._Changed
            operations[wage["operation"]] = operations.get(wage["operation"], Decimal(0)) + wage_amount
            operations["未归类"] = operations.get("未归类", Decimal(0)) + base_amount
            sources.append({"salary_slip": slip["name"], "salary_slip_version": slip["version"],
                "employee": slip["employee"], "employee_name": slip["employee_name"],
                "operation": wage["operation"], "amount": _money(source_amount),
                "wage_entry": wage["name"], "wage_entry_version": wage["modified"],
                "additional_salary": child["additional_salary"],
                "additional_salary_version": additional["modified"]})
    _compare_snapshot(snapshot, capture)
    return {"settlements": {key: _money(value) for key, value in totals.items()},
        "department": _chart(departments, totals["gross_pay"]),
        "group": _chart(groups, totals["gross_pay"]), "operation": _chart(operations, totals["gross_pay"]),
        "sources": sources, "slips": slips, "salary_slips": selected,
        "notes": ["仅含筛选范围内已提交工资条；取消工资条不计入。",
            "净应发与舍入净应发是工资结算金额，不表示已付款。",
            "部门取工资条保存的部门；未记录部门归入未归类。",
            "没有可靠历史分组快照，全部收入按未归类列示；不按当前员工档案补推。",
            "工序金额只计入已审核工资条附加薪资精确引用的已审核计薪记录；其他收入不摊派。"]}


def _chart(values, total):
    return [{"name": name, "amount": _money(amount),
        "share_percent": format((amount * 100 / total).quantize(Decimal("0.01")) if total else Decimal(0), "f")}
        for name, amount in sorted(values.items(), key=lambda pair: (-pair[1], pair[0]))]


def _result_version(result):
    """Bind print/export to the exact queried values and saved identities."""
    return hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False,
        allow_nan=False).encode("utf-8")).hexdigest()


@frappe.whitelist(methods=["GET"])
def query_salary_analysis(context):
    context, employee, currency = _context(context)
    requested_currency = currency
    if frappe.session.user == "Guest":
        _deny()
    try:
        before = H._permission_state()
        if employee:
            capture = H._Capture()
            capture.project("Employee", employee, {"name"}, select=True)
        if currency:
            capture = H._Capture()
            capture.project("Currency", currency, {"name"}, select=True)
        snapshot = H._stable_snapshot(context)
        currencies = sorted({item["row"]["currency"] for item in snapshot["rows"]
            if not employee or item["row"]["employee"] == employee})
        if currency and currency not in currencies:
            # An explicitly selected currency with no matching slips remains a
            # valid empty result, but it must still be a readable Currency.
            pass
        elif not currency and len(currencies) > 1:
            if before != H._permission_state():
                raise H._Changed
            return {"readonly": True, "scope": SCOPE,
                "context": {**context, "employee": employee, "currency": ""},
                "requires_currency": True, "currencies": currencies}
        if not currency and len(currencies) == 1:
            currency = currencies[0]
        with localcontext() as decimal_context:
            decimal_context.prec = 1000
            first = _build(snapshot, employee, currency)
            second = _build(snapshot, employee, currency)
        if first != second or before != H._permission_state():
            raise H._Changed
        result = {"readonly": True, "scope": SCOPE,
            "context": {**context, "employee": employee, "currency": requested_currency},
            "currency": currency, **second}
        result["result_version"] = _result_version(result)
        return result
    except (H._Unavailable, H._Unclassified, frappe.PermissionError,
            frappe.DoesNotExistError, ValueError, TypeError, OverflowError):
        _deny()
    except H._Changed:
        frappe.throw("工资条、计薪来源或读取权限已变化，请重新查询")
