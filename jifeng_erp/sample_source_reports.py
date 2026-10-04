"""Exact native Report execution guards and four fixed SQL scopes.

This is a deny-only boundary at Report.execute_* before any rows/charts are
returned. Six exact Script scopes are provided by the companion module. This
is not a global SQL permission filter; custom aliases and existing Prepared
Report files still require their separately verified consumer boundaries.

The private observer records actual completed source reads in each pre/post
check, without granting permissions or caching an authorization result. The
sample Sources.source and source _check callbacks must be installed together.
"""

from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import importlib
import inspect
from pathlib import Path

import frappe

from jifeng_erp import sample_source_permissions as sources
from jifeng_erp.flow_quantity import check_quantity_fields
from jifeng_erp.sample_orders import _freeze


KNOWN_REPORTS = frozenset({
    "Open Work Orders", "Completed Work Orders", "Work Orders in Progress",
    "Issued Items Against Work Order", "Work Order Summary", "Production Analytics",
    "Sales Order Analysis", "Lost Quotations", "Quotation Trends", "Sales Order Trends",
})
_DENIED = "无法完整读取报表涉及的样品及其来源，请核对来源读取权限"
_CHANGED = "报表来源或定义已变化，请重新读取"
_OBSERVING = ContextVar("jifeng_sample_report_dependencies", default=None)
_EXECUTING = ContextVar("jifeng_sample_reports_executing", default=frozenset())


@dataclass
class ReportDependency:
    """Trusted selector descriptor, never a caller-provided bypass or grant."""

    owner: tuple
    doctype: str
    kind: str = "document"
    name: str | None = None
    filters: dict | None = None
    fields: tuple = ()
    parenttype: str | None = None
    bound_child: tuple | None = None
    bindings: dict = field(default_factory=dict)
    same_company: bool = False
    native_read: bool = True
    observe_only: bool = False


@dataclass
class ReportScope:
    rows: list
    parents: list
    fields: dict = field(default_factory=dict)
    dependencies: list = field(default_factory=list)


def _deny(message=_DENIED):
    frappe.throw(message, frappe.PermissionError)


def _require(value, message=_DENIED):
    if not value:
        _deny(message)


def observe_dependency(document, *, fields=None, tables=None, permission=None,
                       completed_source=False):
    """Internal callback after a successful source read; outside a frame no-op.

    Full document bytes/versions and the actual field/table read contract are
    observed. This does not sign off an incomplete recursive authorization.
    Only the final successful source _check emits completed_source=True.
    """
    frame = _OBSERVING.get()
    if frame is None:
        return
    _require(frame["user"] == frappe.session.user)
    key = (document.doctype, document.get("name"))
    _require(all(isinstance(value, str) and value for value in key))
    state = _freeze(document)
    previous = frame["documents"].get(key)
    _require(previous is None or previous["state"] == state, _CHANGED)
    if previous is None:
        previous = {"state": state, "reads": set(), "completed_source": False}
        frame["documents"][key] = previous
    selected = tuple(sorted(fields or ()))
    table_contract = tuple(sorted((name, childtype, tuple(sorted(columns)))
                                  for name, childtype, columns in tables or ()))
    previous["reads"].add((selected, table_contract, permission))
    previous["completed_source"] |= bool(completed_source)


_WO_FIELDS = ("name", "creation", "production_item", "qty", "produced_qty", "company", "docstatus")
_WO_SELECT = "SELECT wo.name AS work_order FROM `tabWork Order` wo WHERE "
_OPEN_SQL = (_WO_SELECT + "wo.docstatus=1 AND ifnull(wo.produced_qty,0)<wo.qty "
             "AND NOT EXISTS (SELECT name FROM `tabStock Entry` WHERE work_order=wo.name)")
_COMPLETED_SQL = _WO_SELECT + "wo.docstatus=1 AND ifnull(wo.produced_qty,0)>=wo.qty"
_PROGRESS_SQL = (_WO_SELECT + "wo.docstatus=1 AND wo.status!='Stopped' "
                 "AND ifnull(wo.produced_qty,0)<wo.qty "
                 "AND EXISTS (SELECT name FROM `tabStock Entry` WHERE work_order=wo.name)")
_ISSUED_SQL = """SELECT wo.name AS work_order, ste.name AS stock_entry,
ste_item.name AS stock_entry_detail, ste_item.item_code, ste_item.s_warehouse
FROM `tabStock Entry` ste, `tabStock Entry Detail` ste_item, `tabWork Order` wo
WHERE ifnull(ste.work_order,'')!='' AND ste.name=ste_item.parent
AND ste.work_order=wo.name AND ste.docstatus=1
AND ste.purpose='Material Transfer for Manufacture'"""
_STE_FIELDS = ("name", "work_order", "posting_date", "company", "docstatus", "purpose", "items")
_DETAIL_FIELDS = ("name", "parent", "parenttype", "parentfield", "item_code", "description",
                  "transfer_qty", "stock_uom", "amount", "serial_no", "s_warehouse", "t_warehouse")
_BIN_FIELDS = ("item_code", "warehouse", "projected_qty")


def _work_orders(sql, *, progress=False, existence=False):
    rows = frappe.db.sql(sql, as_dict=True)
    return ReportScope(rows=rows, parents=[("Work Order", row["work_order"]) for row in rows],
                       fields={"Work Order": _WO_FIELDS + (("status",) if progress else ())},
                       dependencies=[ReportDependency(("Work Order", row["work_order"]), "Stock Entry",
                                                      kind="rows", filters={"work_order": row["work_order"]},
                                                      fields=("name", "work_order", "docstatus"),
                                                      native_read=False, observe_only=True)
                                     for row in rows] if existence else [])


def select_open_work_orders(filters):
    return _work_orders(_OPEN_SQL, existence=True)


def select_completed_work_orders(filters):
    return _work_orders(_COMPLETED_SQL)


def select_work_orders_in_progress(filters):
    return _work_orders(_PROGRESS_SQL, progress=True, existence=True)


def select_issued_items_against_work_order(filters):
    rows = frappe.db.sql(_ISSUED_SQL, as_dict=True)
    dependencies = []
    for row in rows:
        owner = ("Work Order", row["work_order"])
        dependencies.extend([
            ReportDependency(owner, "Stock Entry", name=row["stock_entry"], fields=_STE_FIELDS,
                             same_company=True, bindings={"work_order": owner[1]}),
            ReportDependency(owner, "Stock Entry Detail", name=row["stock_entry_detail"],
                             fields=_DETAIL_FIELDS, parenttype="Stock Entry",
                             bound_child=("Stock Entry", row["stock_entry"], "items"),
                             native_read=False),
            ReportDependency(owner, "Bin", kind="rows",
                             filters={"item_code": row["item_code"], "warehouse": row["s_warehouse"]},
                             fields=_BIN_FIELDS),
        ])
    return ReportScope(rows=rows, parents=[("Work Order", row["work_order"]) for row in rows],
                       fields={"Work Order": ("name", "production_item", "company")},
                       dependencies=dependencies)


def _query_spec(query_sha256, selector):
    return {"kind": "Query Report", "ref_doctype": "Work Order", "module": "Manufacturing",
            "is_standard": "Yes", "query_sha256": query_sha256, "selector": selector}


# Exact UTF-8 native Report.query bytes, including its terminal whitespace.
REPORT_SPECS = {
    "Open Work Orders": _query_spec("7e89508619d8c14b5637e8e6bb0365916ceea798e63340e4be0ab8231901f24e", select_open_work_orders),
    "Completed Work Orders": _query_spec("87f24e1a44a548974d315ae0a077c731dc90db771c105b8ac3af7be61b0ac3ce", select_completed_work_orders),
    "Work Orders in Progress": _query_spec("a5b3d1de5d0dcfa4f847ae6ce2d410d98e27ce6f7e94b11ee82b2891b2874318", select_work_orders_in_progress),
    "Issued Items Against Work Order": _query_spec("1b4dc8a8203f363f0affca8fff22ec0471d063e55d527a0ef153b296caefaf72", select_issued_items_against_work_order),
}


def _definition(document, kind, spec):
    _require(document.get("name") in KNOWN_REPORTS and document.get("report_name") == document.name, _CHANGED)
    for key, expected in (("report_type", kind), ("ref_doctype", spec["ref_doctype"]),
                          ("module", spec["module"]), ("is_standard", spec["is_standard"])):
        _require(document.get(key) == expected, _CHANGED)
    _require(kind == spec["kind"] and not document.get("reference_report")
             and not document.get("report_script") and not document.get("snapshot_report"), _CHANGED)
    if kind == "Query Report":
        query = document.get("query")
        _require(isinstance(query, str), _CHANGED)
        fingerprint = hashlib.sha256(query.encode("utf-8")).hexdigest()
        _require(fingerprint == spec["query_sha256"], _CHANGED)
    else:
        module = importlib.import_module(spec["execute_module"])
        execute = document.get_module_method("execute")
        origin = Path(module.__file__)
        _require(origin.is_file() and not origin.is_symlink(), _CHANGED)
        path = origin.resolve()
        _require(execute is getattr(module, "execute", None)
                 and execute.__module__ == spec["execute_module"]
                 and Path(inspect.getfile(execute)).resolve() == path
                 and path.is_file() and not path.is_symlink()
                 and hashlib.sha256(path.read_bytes()).hexdigest() == spec["module_sha256"], _CHANGED)
        fingerprint = spec["module_sha256"]
        for name, expected in spec.get("dependency_module_sha256", {}).items():
            dependency_module = importlib.import_module(name)
            dependency_path = Path(dependency_module.__file__)
            _require(dependency_path.is_file() and not dependency_path.is_symlink()
                     and hashlib.sha256(dependency_path.read_bytes()).hexdigest() == expected, _CHANGED)
    # This signs the execution definition, not cached file provenance. Native
    # automatic prepared-report timing/modified flags are intentionally excluded.
    output_definition = _freeze({field: document.get(field) for field in
                                 ("roles", "columns", "filters", "add_total_row", "add_translate_data", "json")})
    return (document.name, kind, document.ref_doctype, document.module, document.is_standard,
            fingerprint, output_definition)


def _definition_pair(report, kind, spec):
    live = _definition(report, kind, spec)
    stored = _definition(frappe.get_doc("Report", report.name), kind, spec)
    _require(live == stored, _CHANGED)
    return live


def _dependency(dependency, owner, user):
    _require(isinstance(dependency, ReportDependency) and dependency.kind in {"document", "rows"})
    if dependency.kind == "rows":
        raw = frappe.get_all(dependency.doctype, filters=dependency.filters,
                             fields=sorted({"name", "modified", *dependency.fields}), order_by="name asc")
        if dependency.observe_only:
            # EXISTS contributes identity/state, but does not project a Stock
            # Entry payload. No new Stock Entry authorization is implied.
            _require(not dependency.native_read and not dependency.bound_child and not dependency.bindings)
            return (dependency.owner, dependency.doctype, dependency.kind, _freeze(dependency.filters),
                    tuple(_freeze(row) for row in raw), ())
        docs = []
        for row in raw:
            document = sources._reference(dependency.doctype, row["name"])
            _require(document is not None, _CHANGED)
            docs.append(document)
        states = tuple(_freeze(row) for row in raw)
    else:
        _require(not dependency.observe_only)
        document = sources._reference(dependency.doctype, dependency.name)
        _require(document is not None, _CHANGED)
        docs, states = [document], ()
    frozen = []
    for document in docs:
        if dependency.bound_child:
            expected_type, expected_parent, expected_field = dependency.bound_child
            if expected_parent is None:
                expected_parent = document.get("parent")
            _require(isinstance(expected_parent, str) and expected_parent)
            _require((document.get("parenttype"), document.get("parent"), document.get("parentfield"))
                     == (expected_type, expected_parent, expected_field))
            parent = sources._child_parent(document)
            _require(parent.get("company") == owner.get("company"))
            sources._native_read(parent, user)
            frozen.append(((parent.doctype, parent.name), _freeze(parent)))
        if dependency.same_company:
            _require(document.get("company") == owner.get("company"))
        for key, expected in dependency.bindings.items():
            _require(document.get(key) == expected)
        if dependency.native_read:
            sources._native_read(document, user)
        check_quantity_fields(dependency.doctype, dependency.fields, parenttype=dependency.parenttype)
        frozen.append(((document.doctype, document.name), _freeze(document)))
    return (dependency.owner, dependency.doctype, dependency.kind, _freeze(dependency.filters),
            states, tuple(frozen))


def _capture(spec, filters, user):
    _require(user == frappe.session.user and _OBSERVING.get() is None)
    scope = spec["selector"](filters)
    _require(isinstance(scope, ReportScope))
    frame = {"user": user, "documents": {}}
    token = _OBSERVING.set(frame)
    try:
        protected = {}
        for doctype, name in dict.fromkeys(scope.parents):
            _require(doctype in sources.PARENTS and isinstance(name, str) and name)
            document = sources._reference(doctype, name)
            _require(document is not None, _CHANGED)
            if not sources._scope(document):
                continue
            _require(sources._key(document, user) not in sources._AUTHORIZING.get())
            sources._check(document, user)
            check_quantity_fields(doctype, scope.fields.get(doctype, ()))
            observed = frame["documents"].get((doctype, name))
            _require(observed and observed["completed_source"], _CHANGED)
            company = frame["documents"].get(("Company", document.get("company")))
            _require(company and any(read[2] == "select" for read in company["reads"]), _CHANGED)
            protected[doctype, name] = document
        dependencies = []
        for dependency in scope.dependencies:
            _require(isinstance(dependency, ReportDependency) and dependency.owner in scope.parents)
            owner = protected.get(dependency.owner)
            if owner is not None:
                dependencies.append(_dependency(dependency, owner, user))
        _require(user == frappe.session.user)
        records = tuple(sorted((key, value["state"], tuple(sorted(value["reads"], key=repr)),
                                value["completed_source"]) for key, value in frame["documents"].items()))
        return (tuple(sorted((_freeze(row) for row in scope.rows), key=repr)),
                tuple(sorted(protected)), records, tuple(sorted(dependencies, key=repr)))
    finally:
        _OBSERVING.reset(token)


def _safe_capture(spec, filters, user):
    try:
        return _capture(spec, filters, user)
    except Exception:
        _deny()


def _execute(report, filters, kind, native):
    name = report.get("name")
    # Exact companion specs are registered separately. A missing registration
    # remains an explicit gap, not a blanket denial or completed coverage claim.
    spec = _spec(name) if name in KNOWN_REPORTS else None
    if spec is None:
        return native(filters)
    user = frappe.session.user
    key = (name, user)
    _require(key not in _EXECUTING.get())
    token = _EXECUTING.set(_EXECUTING.get() | {key})
    try:
        definition = _definition_pair(report, kind, spec)
        # Exact audited native validators can preserve ordinary filter errors;
        # only fixed spec code supplies this function, never caller input.
        if validator := spec.get("validate_filters"):
            validator(filters)
        before = _safe_capture(spec, filters, user)
        result = native(filters)
        # Native Script Reports may legitimately normalize/mutate filters. The
        # before/after exact effective scope, rather than input byte equality,
        # is the required source boundary.
        _require(user == frappe.session.user, _CHANGED)
        _require(_definition_pair(report, kind, spec) == definition, _CHANGED)
        after = _safe_capture(spec, filters, user)
        _require(before == after, _CHANGED)
        return result
    finally:
        _EXECUTING.reset(token)


class SampleSourceReportMixin:
    """Narrow Report controller hooks, preserving original arguments/result."""

    def execute_query_report(self, filters):
        return _execute(self, filters, "Query Report", super().execute_query_report)

    def execute_script_report(self, filters):
        return _execute(self, filters, "Script Report", super().execute_script_report)


def _spec(name):
    if name in REPORT_SPECS:
        return REPORT_SPECS[name]
    # Optional exact companion specs are added by the parallel Script audit;
    # absence is an explicitly unprotected path, never a completed claim.
    try:
        from jifeng_erp.sample_source_report_scripts import get_script_specs
    except ModuleNotFoundError as error:
        if error.name == "jifeng_erp.sample_source_report_scripts":
            return None
        raise
    specs = get_script_specs()
    _require(set(specs) <= KNOWN_REPORTS and not set(specs) & set(REPORT_SPECS))
    return specs.get(name)
