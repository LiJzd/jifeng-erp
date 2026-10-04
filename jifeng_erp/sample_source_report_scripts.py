"""Fixed native Script Report scopes; no result filtering or read grants.

The companion Report mixin authorizes protected owners before native execution.
These selectors preserve the audited native predicates and raw join identities,
including rows that native aggregation would collapse. Nothing is public here.
"""

import copy
import hashlib
import importlib
import inspect
from pathlib import Path
from types import CodeType, FunctionType

import frappe

from jifeng_erp.sample_source_reports import ReportDependency, ReportScope


_DENIED = "报表定义或来源已变化，请重新核对来源读取权限"
_DATA = "frappe.utils.data"
_DATA_SHA = "432faeec793c21a62085e311979ac6201b31b1cc0cee9e7310b6d75053063d24"
_TRENDS = "erpnext.controllers.trends"
_TRENDS_SHA = "209d8405570ea2060f9bc2b87bdffd9fd91f9c483ffd8d4e49b592b23e50612a"
_STOCK_ANALYTICS = "erpnext.stock.report.stock_analytics.stock_analytics"
_STOCK_ANALYTICS_SHA = "fe1461af36773e12e98c8c7e41f6b7c1e1475e913a3f1dd323717c6f9a02f17e"
_ACCOUNTS_UTILS = "erpnext.accounts.utils"
_ACCOUNTS_UTILS_SHA = "d9a747b7ca2bf116c67d1ae0e8cf594d98763c3fda985768c2fe5a393f1a341f"
_ERP_SHA = "bdf25195f6f66e89b1752739c4519118218b47c03c84c842c83d107b9a26cb9b"
_FRAPPE_SHA = "c6e07341358f009eace74794547192a8b0e653694e4173727e851e48aa0112c4"
_TYPING = "frappe.utils.typing_validations"
_TYPING_SHA = "d86b35d69b3688cf4550f6d666171b43c3cd10bc81f8794b76f4f58381198dc4"
_IDENTITY = ("name", "parent", "parenttype", "parentfield")


def _deny():
    frappe.throw(_DENIED, frappe.PermissionError)


def _plain_origin(fn, module, path, method, source):
    """Plain functions must still be the executable code of the pinned file."""
    if (not isinstance(fn, FunctionType) or fn.__module__ != module.__name__
            or fn.__name__ != method or fn.__code__.co_name != method
            or fn.__globals__ is not vars(module) or hasattr(fn, "__wrapped__")
            or Path(fn.__code__.co_filename).resolve() != path):
        return False
    compiled = compile(source, fn.__code__.co_filename, "exec", dont_inherit=True)
    return any(isinstance(code, CodeType) and code.co_name == method and code == fn.__code__
               for code in compiled.co_consts)


def _fiscal_whitelist_origin(fn, module, path, source):
    """Accept only the audited single Frappe whitelist type-validation wrapper.

    A generic inspect.unwrap would accept extra or substituted wrappers. Here
    the exact factory code, globals, closure, registered function, and original
    executable code must all agree with the pinned native implementation.
    """
    framework = _native_module("frappe", _FRAPPE_SHA, ("whitelist", "_in_request_or_test"))
    typing = _native_module(_TYPING, _TYPING_SHA,
                            ("validate_argument_types", "transform_parameter_types"))
    if not isinstance(fn, FunctionType):
        return False
    raw = getattr(fn, "__wrapped__", None)
    if not _plain_origin(raw, module, path, "get_fiscal_year", source):
        return False
    if (raw.__defaults__ != (None, None, "Date", 1, None, False, None, True, False)
            or raw.__kwdefaults__ is not None or raw.__annotations__ != {}):
        return False
    factory = typing.validate_argument_types
    wrappers = [code for code in factory.__code__.co_consts
                if isinstance(code, CodeType) and code.co_name == "wrapper"]
    if (len(wrappers) != 1 or fn.__code__ is not wrappers[0]
            or fn.__globals__ is not vars(typing)
            or fn.__module__ != module.__name__ or fn.__name__ != raw.__name__
            or fn.__qualname__ != raw.__qualname__ or fn.__annotations__ != raw.__annotations__
            or fn.__defaults__ is not None or fn.__kwdefaults__ is not None
            or fn.__code__.co_freevars != ("apply_condition", "func")
            or not fn.__closure__ or len(fn.__closure__) != 2
            or fn not in framework.whitelisted):
        return False
    try:
        captured = dict(zip(fn.__code__.co_freevars,
                            (cell.cell_contents for cell in fn.__closure__), strict=True))
    except ValueError:
        return False
    return (captured["func"] is raw
            and captured["apply_condition"] is framework._in_request_or_test)


def _native_module(name, digest, methods=()):
    """Check the real source and callable origin on every scope observation."""
    module = importlib.import_module(name)
    path = inspect.getsourcefile(module)
    if not path or module.__name__ != name:
        _deny()
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        _deny()
    path = path.resolve()
    try:
        source = path.read_bytes()
    except OSError:
        _deny()
    if hashlib.sha256(source).hexdigest() != digest:
        _deny()
    for method in methods:
        fn = getattr(module, method, None)
        if (name, method) == (_ACCOUNTS_UTILS, "get_fiscal_year"):
            valid = _fiscal_whitelist_origin(fn, module, path, source)
        else:
            valid = _plain_origin(fn, module, path, method, source)
        if not valid:
            _deny()
    return module


def _aliases(module, expected):
    for name, value in expected.items():
        if getattr(module, name, None) is not value:
            _deny()


def _filters(filters):
    # Report.execute_module itself wraps filters in frappe._dict. Keep caller
    # objects untouched; native defaults and chart helpers may mutate their copy.
    return frappe._dict(copy.deepcopy(filters or {}))


def _parents(doctype, rows, field="name"):
    return list(dict.fromkeys((doctype, row[field]) for row in rows))


def _document(owner, doctype, name, fields, **kwargs):
    return ReportDependency(kind="document", owner=owner, doctype=doctype,
                            name=name, fields=tuple(fields), **kwargs)


def _rows(owner, doctype, filters, fields, **kwargs):
    return ReportDependency(kind="rows", owner=owner, doctype=doctype,
                            filters=filters, fields=tuple(fields), **kwargs)


def _field_union(*groups):
    return tuple(dict.fromkeys(field for group in groups for field in group))


_WO_SUMMARY_MODULE = "erpnext.manufacturing.report.work_order_summary.work_order_summary"
_WO_SUMMARY_SHA = "967a62d34499d4f198606122590be74a27005611a5f1262da0f45f46ddc8ea72"
_WO_SUMMARY_FIELDS = (
    "name", "status", "sales_order", "production_item", "qty", "creation",
    "produced_qty", "planned_start_date", "planned_end_date", "actual_start_date",
    "actual_end_date", "lead_time", "company", "docstatus",
)


def work_order_summary_scope(filters):
    native = _native_module(_WO_SUMMARY_MODULE, _WO_SUMMARY_SHA, ("execute", "get_data"))
    dates = _native_module(_DATA, _DATA_SHA, ("today", "date_diff", "getdate", "flt"))
    periods = _native_module(_STOCK_ANALYTICS, _STOCK_ANALYTICS_SHA,
                             ("get_period", "get_period_date_ranges"))
    _aliases(native, {"today": dates.today, "date_diff": dates.date_diff,
                      "getdate": dates.getdate, "flt": dates.flt,
                      "get_period": periods.get_period,
                      "get_period_date_ranges": periods.get_period_date_ranges})
    effective = _filters(filters)
    if not effective.get("age"):
        effective["age"] = 0
    query_filters = {"docstatus": ("<", 2)}
    for field in ("sales_order", "production_item"):
        if effective.get(field):
            query_filters[field] = ("in", effective.get(field))
    for field in ("status", "company"):
        if effective.get(field):
            query_filters[field] = effective.get(field)
    if effective.get("based_on") == "Planned Date":
        query_filters["planned_start_date"] = (">=", effective.get("from_date"))
        query_filters["planned_end_date"] = ("<=", effective.get("to_date"))
    elif effective.get("based_on") == "Actual Date":
        query_filters["actual_start_date"] = (">=", effective.get("from_date"))
        query_filters["actual_end_date"] = ("<=", effective.get("to_date"))
    else:
        query_filters["creation"] = ("between", [effective.get("from_date"),
                                                  effective.get("to_date")])
    raw = frappe.get_all("Work Order", fields=list(_WO_SUMMARY_FIELDS),
                         filters=query_filters, order_by="planned_start_date asc")
    rows = []
    for row in raw:
        observed = dict(row)
        age = 0 if row.get("status") == "Completed" else dates.date_diff(
            dates.today(), row.get("actual_start_date") or row.get("planned_start_date"))
        if effective.get("age") <= age:
            observed["age"] = age
            rows.append(observed)
    parents = _parents("Work Order", rows)
    if parents:
        chart = effective.get("charts_based_on")
        if chart == "Status":
            rows.append({"summary_status_options": frappe.get_meta("Work Order").get_options("status")})
        elif chart != "Age":
            monthly = _filters(effective)
            monthly.range = "Monthly"  # native prepare_chart_data forces this
            ranges = periods.get_period_date_ranges(monthly)
            labels = [periods.get_period(end, monthly) for _start, end in ranges]
            rows.append({"summary_quantity_periods": {"ranges": ranges, "labels": labels,
                                                        "today": dates.today()}})
    return ReportScope(rows=rows, parents=parents,
                       fields={"Work Order": _WO_SUMMARY_FIELDS}, dependencies=[])


_PRODUCTION_MODULE = "erpnext.manufacturing.report.production_analytics.production_analytics"
_PRODUCTION_SHA = "348143a07edfedffe8d6a9edd4881e152f31faddb1f50dd685a53b29013e04fc"
_PRODUCTION_FIELDS = ("name", "company", "docstatus", "creation", "actual_end_date",
                      "planned_end_date", "status")


def production_analytics_scope(filters):
    native = _native_module(_PRODUCTION_MODULE, _PRODUCTION_SHA, ("execute", "get_work_orders"))
    periods = _native_module(_STOCK_ANALYTICS, _STOCK_ANALYTICS_SHA,
                             ("get_period", "get_period_date_ranges", "get_period_columns"))
    dates = _native_module(_DATA, _DATA_SHA, ("today", "getdate"))
    fiscal = _native_module(_ACCOUNTS_UTILS, _ACCOUNTS_UTILS_SHA, ("get_fiscal_year",))
    _aliases(native, {"today": dates.today, "getdate": dates.getdate,
                      "get_period": periods.get_period,
                      "get_period_date_ranges": periods.get_period_date_ranges,
                      "get_period_columns": periods.get_period_columns})
    _aliases(periods, {"get_fiscal_year": fiscal.get_fiscal_year})
    effective = _filters(filters)
    rows = frappe.db.sql("""
        SELECT wo.name, wo.company, wo.docstatus, wo.creation,
               wo.actual_end_date, wo.planned_end_date, wo.status
        FROM `tabWork Order` wo
        WHERE wo.docstatus = 1 AND wo.company = %(company)s
          AND (wo.creation BETWEEN %(from_date)s AND %(to_date)s
               OR wo.actual_end_date BETWEEN %(from_date)s AND %(to_date)s)
    """, {"company": effective.get("company"), "from_date": effective.get("from_date"),
           "to_date": effective.get("to_date")}, as_dict=True)
    parents = _parents("Work Order", rows)
    dependencies = []
    if parents:
        ranges = periods.get_period_date_ranges(effective)
        labels = [periods.get_period(end, effective) for _start, end in ranges]
        fiscal_reads = []
        if effective.get("range") == "Yearly":
            fiscal_reads.append(fiscal.get_fiscal_year(effective.get("from_date")))
        if effective.get("range") not in ("Weekly", "Monthly", "Quarterly"):
            fiscal_reads.extend(fiscal.get_fiscal_year(end, company=effective.get("company"))
                                for _start, end in ranges)
        for owner in parents:
            for name, start, end in fiscal_reads:
                dependencies.append(_document(owner, "Fiscal Year", name,
                                              ("name", "disabled", "year_start_date", "year_end_date", "companies"),
                                              bindings={"year_start_date": start, "year_end_date": end}))
        rows.append({"production_periods": {"ranges": ranges, "labels": labels,
                                              "today": dates.today(), "fiscal_reads": fiscal_reads}})
    return ReportScope(rows=rows, parents=parents,
                       fields={"Work Order": _PRODUCTION_FIELDS}, dependencies=dependencies)


def validate_production_analytics_filters(filters):
    _native_module(_PRODUCTION_MODULE, _PRODUCTION_SHA, ("execute",))
    periods = _native_module(_STOCK_ANALYTICS, _STOCK_ANALYTICS_SHA, ("get_period_columns",))
    periods.get_period_columns(_filters(filters))


_SO_ANALYSIS_MODULE = "erpnext.selling.report.sales_order_analysis.sales_order_analysis"
_SO_ANALYSIS_SHA = "06949fb0837a156704a0e59c5747641e9636c54aa404cf7fd3aabb8ff530455e"
_SO_FIELDS = ("name", "company", "transaction_date", "status", "customer",
               "conversion_rate", "items")
_SO_ITEM_FIELDS = _field_union(_IDENTITY, (
    "delivery_date", "item_code", "uom", "qty", "delivered_qty", "base_amount",
    "base_rate", "billed_amt", "warehouse", "description",
))


def _sales_order_analysis_context(filters):
    native = _native_module(_SO_ANALYSIS_MODULE, _SO_ANALYSIS_SHA,
                            ("execute", "validate_filters", "get_conditions"))
    dates = _native_module(_DATA, _DATA_SHA, ("date_diff", "flt"))
    erp = _native_module("erpnext", _ERP_SHA, ("get_default_company",))
    _aliases(native, {"date_diff": dates.date_diff, "flt": dates.flt, "erpnext": erp})
    if not filters:
        return native, None
    effective = _filters(filters)
    effective.company = effective.get("company") or erp.get_default_company()
    native.validate_filters(effective)
    return native, effective


def validate_sales_order_analysis_filters(filters):
    _sales_order_analysis_context(filters)


def sales_order_analysis_scope(filters):
    native, effective = _sales_order_analysis_context(filters)
    if effective is None:
        return ReportScope(rows=[], parents=[], fields={}, dependencies=[])
    conditions = native.get_conditions(effective)
    rows = frappe.db.sql(f"""
        SELECT so.name AS sales_order, soi.name AS sales_order_item,
               soi.parent AS so_item_parent, soi.parenttype AS so_item_parenttype,
               soi.parentfield AS so_item_parentfield,
               sii.name AS sales_invoice_item, sii.parent AS sales_invoice,
               sii.parenttype AS invoice_item_parenttype,
               sii.parentfield AS invoice_item_parentfield,
               sii.so_detail AS invoice_so_detail, sii.docstatus AS invoice_item_docstatus,
               sii.qty AS invoice_qty
        FROM `tabSales Order` so, `tabSales Order Item` soi
        LEFT JOIN `tabSales Invoice Item` sii
          ON sii.so_detail = soi.name AND sii.docstatus = 1
        WHERE soi.parent = so.name
          AND so.status NOT IN ('Stopped', 'On Hold') AND so.docstatus = 1
          {conditions}
    """, effective, as_dict=True)
    parents = _parents("Sales Order", rows, "sales_order")
    dependencies = [_document(owner, "Company", effective.company, ("name", "default_currency"))
                    for owner in parents]
    for row in rows:
        owner = ("Sales Order", row["sales_order"])
        child = row["sales_order_item"]
        dependencies.append(_document(owner, "Sales Order Item", child, _SO_ITEM_FIELDS,
                                      parenttype="Sales Order", bound_child=(*owner, "items")))
        dependencies.append(_rows(owner, "Sales Invoice Item",
                                  {"so_detail": child, "docstatus": 1},
                                  _field_union(_IDENTITY, ("so_detail", "docstatus", "qty")),
                                  parenttype="Sales Invoice", native_read=False,
                                  bound_child=("Sales Invoice", None, "items"),
                                  bindings={"so_detail": child, "docstatus": 1}))
        if row.get("sales_invoice_item"):
            invoice = row.get("sales_invoice")
            dependencies.extend([
                _document(owner, "Sales Invoice", invoice, ("name", "company", "items"),
                          same_company=True),
                _document(owner, "Sales Invoice Item", row["sales_invoice_item"],
                          _field_union(_IDENTITY, ("so_detail", "docstatus", "qty")),
                          parenttype="Sales Invoice", bound_child=("Sales Invoice", invoice, "items"),
                          bindings={"so_detail": child}),
            ])
    if rows and not effective.get("group_by_item"):
        names = list(dict.fromkeys(row["sales_order"] for row in rows))
        elapsed = frappe.db.sql("""
            SELECT so.name AS sales_order, soi.name AS elapsed_so_item,
                   soi.parent AS elapsed_so_parent, soi.parenttype AS elapsed_so_parenttype,
                   soi.parentfield AS elapsed_so_parentfield, soi.item_code AS elapsed_item_code,
                   dni.name AS delivery_note_item, dni.parent AS delivery_note,
                   dni.parenttype AS delivery_item_parenttype,
                   dni.parentfield AS delivery_item_parentfield, dni.so_detail AS delivery_so_detail,
                   dn.docstatus AS delivery_docstatus, dn.posting_date AS delivery_posting_date
            FROM `tabSales Order` so
            INNER JOIN `tabSales Order Item` soi ON soi.parent = so.name
            LEFT JOIN `tabDelivery Note Item` dni ON dni.so_detail = soi.name
            LEFT JOIN `tabDelivery Note` dn ON dni.parent = dn.name
            WHERE so.name IN %(sales_orders)s AND dn.docstatus = 1
        """, {"sales_orders": names}, as_dict=True)
        for row in elapsed:
            # Use a tagged second raw set, not a guessed join to first-query
            # warehouse/item rows. Native elapsed reads every item of these SOs.
            rows.append({"elapsed_source": dict(row)})
            owner = ("Sales Order", row["sales_order"])
            child = row["elapsed_so_item"]
            delivery = row["delivery_note"]
            dependencies.extend([
                _document(owner, "Sales Order Item", child,
                          _field_union(_IDENTITY, ("item_code",)), parenttype="Sales Order",
                          bound_child=(*owner, "items")),
                _document(owner, "Delivery Note", delivery,
                          ("name", "company", "docstatus", "posting_date", "items"), same_company=True),
                _document(owner, "Delivery Note Item", row["delivery_note_item"],
                          _field_union(_IDENTITY, ("so_detail",)), parenttype="Delivery Note",
                          bound_child=("Delivery Note", delivery, "items"),
                          bindings={"so_detail": child}),
            ])
    return ReportScope(rows=rows, parents=parents, fields={"Sales Order": _SO_FIELDS},
                       dependencies=dependencies)


_LOST_MODULE = "erpnext.selling.report.lost_quotations.lost_quotations"
_LOST_SHA = "f46d52ca20c1861961eb4238643ec517f7d97dc1d829a52e8f536be42a07cc82"


def _lost_quotations_context(filters):
    native = _native_module(_LOST_MODULE, _LOST_SHA, ("execute", "get_data"))
    dates = _native_module(_DATA, _DATA_SHA, ("get_timespan_date_range",))
    _aliases(native, {"get_timespan_date_range": dates.get_timespan_date_range})
    effective = _filters(filters)
    from_date, to_date = dates.get_timespan_date_range(effective.get("timespan").lower())
    dimension = {
        "Lost Reason": ("Quotation Lost Reason Detail", "lost_reason", "lost_reasons"),
        "Competitor": ("Competitor Detail", "competitor", "competitors"),
    }.get(effective.get("group_by"))
    if dimension is None:
        frappe.throw(frappe._("Invalid Group By"))
    return effective, from_date, to_date, dimension


def validate_lost_quotations_filters(filters):
    _lost_quotations_context(filters)


def lost_quotations_scope(filters):
    effective, from_date, to_date, dimension = _lost_quotations_context(filters)
    doctype, field, parentfield = dimension
    rows = frappe.db.sql(f"""
        SELECT q.name AS quotation, d.name AS dimension_name,
               d.parent AS dimension_parent, d.parenttype AS dimension_parenttype,
               d.parentfield AS dimension_parentfield, d.`{field}` AS dimension_value
        FROM `tabQuotation` q LEFT JOIN `tab{doctype}` d ON d.parent = q.name
        WHERE q.status = 'Lost' AND q.docstatus = 1
          AND q.transaction_date >= %(from_date)s AND q.transaction_date <= %(to_date)s
          AND q.company = %(company)s
    """, {"from_date": from_date, "to_date": to_date, "company": effective.get("company")},
        as_dict=True)
    parents = _parents("Quotation", rows, "quotation")
    dependencies = []
    for owner in parents:
        dependencies.append(_rows(owner, doctype, {"parent": owner[1]},
                                  _field_union(_IDENTITY, (field,)), parenttype="Quotation",
                                  bound_child=(*owner, parentfield)))
    return ReportScope(rows=rows, parents=parents,
                       fields={"Quotation": ("name", "status", "docstatus", "transaction_date",
                                             "company", "base_net_total", parentfield)},
                       dependencies=dependencies)


_QUOTE_TRENDS_MODULE = "erpnext.selling.report.quotation_trends.quotation_trends"
_QUOTE_TRENDS_SHA = "6c8f056fbf054857f87f415250bb6c9c54213fb0d731e342386f1d478243f15d"
_SO_TRENDS_MODULE = "erpnext.selling.report.sales_order_trends.sales_order_trends"
_SO_TRENDS_SHA = "4be85e1439380b0ddf819e807ead3881eb26cc207dc7490c582411a31567da8f"


def _trends_context(filters, doctype):
    module, digest = (_QUOTE_TRENDS_MODULE, _QUOTE_TRENDS_SHA) if doctype == "Quotation" else (
        _SO_TRENDS_MODULE, _SO_TRENDS_SHA)
    report = _native_module(module, digest, ("execute", "get_chart_data"))
    native = _native_module(_TRENDS, _TRENDS_SHA,
                            ("get_columns", "validate_filters", "get_data"))
    fiscal = _native_module(_ACCOUNTS_UTILS, _ACCOUNTS_UTILS_SHA, ("get_fiscal_year",))
    dates = _native_module(_DATA, _DATA_SHA, ("today", "getdate"))
    erp = _native_module("erpnext", _ERP_SHA, ("get_company_currency",))
    _aliases(report, {"get_columns": native.get_columns, "get_data": native.get_data})
    _aliases(native, {"get_fiscal_year": fiscal.get_fiscal_year, "today": dates.today,
                      "getdate": dates.getdate, "erpnext": erp})
    effective = _filters(filters)
    conditions = native.get_columns(effective, doctype)
    if conditions.get("trans") != doctype:
        _deny()
    return effective, conditions


def validate_quotation_trends_filters(filters):
    _trends_context(filters, "Quotation")


def validate_sales_order_trends_filters(filters):
    _trends_context(filters, "Sales Order")


def _trends_scope(filters, doctype):
    effective, conditions = _trends_context(filters, doctype)
    # This intentionally matches native's exact string comparison. get_columns
    # appends currency; do not silently repair its Project NULL predicate.
    cond = ""
    if conditions["based_on_select"] in ("t1.project,", "t2.project,"):
        cond = " and " + conditions["based_on_select"][:-1] + " IS Not NULL"
    if doctype == "Sales Order" and not effective.get("include_closed_orders"):
        cond += " and t1.status != 'Closed'"
    if doctype == "Quotation" and effective.get("group_by") == "Customer":
        cond += " and t1.quotation_to = 'Customer'"
    fiscal_year = effective.get("fiscal_year")
    start, end = frappe.get_cached_value("Fiscal Year", fiscal_year,
                                        ["year_start_date", "year_end_date"])
    rows = frappe.db.sql(f"""
        SELECT t1.name AS parent_name, t2.name AS item_name,
               t2.parent AS item_parent, t2.parenttype AS item_parenttype,
               t2.parentfield AS item_parentfield
        FROM `tab{doctype}` t1, `tab{doctype} Item` t2 {conditions['addl_tables']}
        WHERE t2.parent = t1.name AND t1.company = %(company)s
          AND t1.transaction_date BETWEEN %(year_start)s AND %(year_end)s
          AND t1.docstatus = 1 {conditions.get('addl_tables_relational_cond', '')} {cond}
    """, {"company": effective.get("company"), "year_start": start, "year_end": end},
        as_dict=True)
    parents = _parents(doctype, rows, "parent_name")
    parent_fields = ["name", "company", "docstatus", "transaction_date", "items"]
    if doctype == "Sales Order" and not effective.get("include_closed_orders"):
        parent_fields.append("status")
    if doctype == "Quotation" and effective.get("group_by") == "Customer":
        parent_fields.append("quotation_to")
    based_on = effective.get("based_on")
    groups = {based_on, effective.get("group_by")}
    child_fields = list(_field_union(_IDENTITY, ("stock_qty", "base_net_amount")))
    if "Item" in groups:
        child_fields.append("item_code")
        if based_on == "Item":
            child_fields.append("item_name")
    if based_on == "Item Group":
        child_fields.append("item_group")
    if "Customer" in groups:
        parent_fields.append("party_name" if doctype == "Quotation" else "customer")
        if based_on == "Customer":
            parent_fields.extend(("customer_name", "territory"))
    if based_on == "Customer Group":
        parent_fields.append("customer_group")
    if based_on == "Territory":
        parent_fields.append("territory")
    if based_on == "Project":
        parent_fields.append("project")
    supplier_join = based_on in ("Supplier", "Supplier Group")
    if supplier_join:
        parent_fields.append("supplier")
    dependencies = []
    for owner in parents:
        dependencies.extend([
            _document(owner, "Fiscal Year", fiscal_year,
                      ("name", "year_start_date", "year_end_date"),
                      bindings={"year_start_date": start, "year_end_date": end}),
            _document(owner, "Company", effective.get("company"), ("name", "default_currency"),
                      bindings={"default_currency": conditions.get("company_currency")}),
        ])
        if supplier_join:
            # Pin the actual Supplier join on this contributing parent. This
            # read is internal enumeration; authorization is owner-conditional.
            supplier = frappe.db.get_value(doctype, owner[1], "supplier")
            dependencies.append(_document(owner, "Supplier", supplier,
                                          ("name", "supplier_group")))
    for row in rows:
        owner = (doctype, row["parent_name"])
        dependencies.append(_document(owner, f"{doctype} Item", row["item_name"], child_fields,
                                      parenttype=doctype, bound_child=(*owner, "items")))
    # Preserve the exact cached inputs as well as the fresh FY/Company versions.
    # A stale cached fiscal range/currency must fail in the companion verifier.
    if parents:
        rows.append({"trends_auxiliary": {"fiscal_year": fiscal_year, "year_start_date": start,
                                           "year_end_date": end, "company": effective.get("company"),
                                           "company_currency": conditions.get("company_currency")}})
    return ReportScope(rows=rows, parents=parents,
                       fields={doctype: _field_union(parent_fields)}, dependencies=dependencies)


def quotation_trends_scope(filters):
    return _trends_scope(filters, "Quotation")


def sales_order_trends_scope(filters):
    return _trends_scope(filters, "Sales Order")


def get_script_specs():
    def spec(module, digest, ref, group, selector, helpers, validator=None):
        return {"kind": "Script Report", "ref_doctype": ref, "module": group,
                "is_standard": "Yes", "execute_module": module,
                "module_sha256": digest, "dependency_module_sha256": helpers,
                "selector": selector, "validate_filters": validator}

    dates = {_DATA: _DATA_SHA}
    decorators = {"frappe": _FRAPPE_SHA, _TYPING: _TYPING_SHA}
    periods = {**dates, **decorators, _STOCK_ANALYTICS: _STOCK_ANALYTICS_SHA,
               _ACCOUNTS_UTILS: _ACCOUNTS_UTILS_SHA}
    trends = {**dates, _TRENDS: _TRENDS_SHA, _ACCOUNTS_UTILS: _ACCOUNTS_UTILS_SHA,
              "erpnext": _ERP_SHA, **decorators}
    return {
        "Work Order Summary": spec(_WO_SUMMARY_MODULE, _WO_SUMMARY_SHA, "Work Order", "Manufacturing",
                                   work_order_summary_scope, periods),
        "Production Analytics": spec(_PRODUCTION_MODULE, _PRODUCTION_SHA, "Work Order", "Manufacturing",
                                     production_analytics_scope, periods, validate_production_analytics_filters),
        "Sales Order Analysis": spec(_SO_ANALYSIS_MODULE, _SO_ANALYSIS_SHA, "Sales Order", "Selling",
                                     sales_order_analysis_scope, {**dates, "erpnext": _ERP_SHA},
                                     validate_sales_order_analysis_filters),
        "Lost Quotations": spec(_LOST_MODULE, _LOST_SHA, "Quotation", "Selling", lost_quotations_scope, dates,
                                validate_lost_quotations_filters),
        "Quotation Trends": spec(_QUOTE_TRENDS_MODULE, _QUOTE_TRENDS_SHA, "Quotation", "Selling",
                                 quotation_trends_scope, trends, validate_quotation_trends_filters),
        "Sales Order Trends": spec(_SO_TRENDS_MODULE, _SO_TRENDS_SHA, "Sales Order", "Selling",
                                   sales_order_trends_scope, trends, validate_sales_order_trends_filters),
    }
