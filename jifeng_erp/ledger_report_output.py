"""Fresh output for five ledgers; cached/file/worker gates are separate.

Native filters, custom columns, row filtering, totals and formatting remain
authoritative. One source guard spans raw execution and the last formatter.
No Report metadata is saved and the native automatic Prepared timer is unused.
"""

from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
import json
from time import monotonic

import frappe

from jifeng_erp.ledger_source_guard import LedgerSourceGuard, _freeze, output_guard
from jifeng_erp.sample_source_report_scripts import _native_module


LEDGER_REPORTS = {
    "Jifeng Purchase Request Ledger": ("purchase_request", "Material Request", "procurement_reports", "purchase_request_ledger"),
    "Jifeng Purchase Receipt Ledger": ("purchase_receipt", "Purchase Receipt", "procurement_reports", "purchase_receipt_ledger"),
    "Jifeng Subcontracting Progress": ("subcontracting_progress", "Subcontracting Order", "subcontracting", "subcontracting_progress"),
    "Jifeng Subcontracting Receipt Ledger": ("subcontracting_receipt", "Subcontracting Receipt", "subcontracting", "subcontracting_receipt_ledger"),
    "Jifeng Subcontracting Material Ledger": ("subcontracting_material", "Stock Entry", "subcontracting_material", "subcontracting_material_ledger"),
}
# Exact entry and dependency bytes are filled from this reviewed candidate.
_MODULE_SHAS = {
    "Jifeng Purchase Request Ledger": "ebb2d38d066fd46491566f7f93db0aab4cbf6b9bb3b3a8b590516b9025fca810",
    "Jifeng Purchase Receipt Ledger": "da3630beb3f41ac6843db0baf3be36b85f165b0c1342ad60fe042ea31126bfc0",
    "Jifeng Subcontracting Progress": "c10c44a4a94c7c98515998a984b8ec897469a331f9d5458828557c94323a1a16",
    "Jifeng Subcontracting Receipt Ledger": "f1dbd202b6f611561c5536867020923c2ef1f09516b0e9e9df97774568b93a6c",
    "Jifeng Subcontracting Material Ledger": "3997ab5d08f43b7ecfb21fdbad8e4c6c53bdfc42a306179082595acc64c63626"
}
_DEPENDENCY_SHAS = {
    "procurement_reports": "6141c7980ee648bb6b25d534bb4306ec08779c78fe3799fcea1bb3bdea4e0c7c",
    "subcontracting": "7bf5cebd4594c7e329c43729b2167eb06e59eb6c506dee22110a58d96ac179c5",
    "subcontracting_material": "90b798c6e712f82b7db4a618ef1631624466374d1f4c5bee85a1bd78fb734e2a",
    "ledger_source_guard": "be5dc67e3914babc7c24042d10f84e3c20f40cf656bf1f596e1e364bed3080ef",
    "inventory_scope": "832a3f8612bac647c5f9a9f807fc82e9555f852b9fe46ba902891d2aec48a4b7"
}
_QUERY_SHA = "cb9a0c1c9e200483777b1708f2bd79173989dd689ab175f307af62e1141c7af5"
_MASK_SHA = "1469a524c4041d4dd6b768475eaf659ba07e9c5aa94774e1004add360a473dda"
_FRAME = ContextVar("jifeng_ledger_fresh_output", default=None)
_DEFINITION_FIELDS = ("name", "report_name", "report_type", "reference_report", "ref_doctype", "module",
                      "is_standard", "disabled", "report_script", "query", "snapshot_report",
                      "roles", "columns", "filters", "add_total_row", "add_translate_data", "json")


def _require(value):
    if not value:
        frappe.throw("报表定义、筛选、来源或权限已变化，请重新查询", frappe.PermissionError)


@dataclass(frozen=True)
class Target:
    requested_name: str
    canonical_name: str
    kind: str
    chain: tuple


@dataclass(frozen=True)
class Resolved:
    target: Target
    report: object
    definition: tuple


def resolve_target(name, *, root=None):
    """Classify before authorization; a hidden ledger cannot become ordinary.

The optional in-memory first document supports binding-change validators.
Canonical five names stay protected even when their stored definition changes.
"""
    _require(isinstance(name, str) and bool(name))
    requested, chain, seen = name, [], set()
    while True:
        _require(name not in seen)
        seen.add(name)
        doc = root if not chain and root is not None else frappe.get_doc("Report", name)
        _require(doc.doctype == "Report" and doc.name == name)
        chain.append(doc)
        if name in LEDGER_REPORTS:
            return Target(requested, name, LEDGER_REPORTS[name][0], tuple(chain))
        if doc.get("report_type") != "Custom Report":
            return None
        name = doc.get("reference_report")
        _require(isinstance(name, str) and bool(name))


def _native():
    return _native_module("frappe.desk.query_report", _QUERY_SHA,
                          ("get_report_doc", "get_report_result", "normalize_result", "get_column_as_dict",
                           "get_filtered_data", "add_total_row", "translate_report_data", "validate_filters_permissions"))


def _definition(target):
    name = target.canonical_name
    kind, ref_doctype, dependency, function = LEDGER_REPORTS[name]
    report = target.chain[-1]
    _require(report.get("report_name") == name and report.get("report_type") == "Script Report"
             and report.get("ref_doctype") == ref_doctype and report.get("module") == "Jifeng ERP"
             and report.get("is_standard") == "Yes" and not report.get("report_script")
             and not report.get("query") and not report.get("reference_report") and not report.get("snapshot_report"))
    module_name = "jifeng_erp.jifeng_erp.report." + name.lower().replace(" ", "_")
    module_name += "." + name.lower().replace(" ", "_")
    module = _native_module(module_name, _MODULE_SHAS[name], ("execute",))
    source = _native_module("jifeng_erp." + dependency, _DEPENDENCY_SHAS[dependency], (function,))
    _require(report.get_module_method("execute") is module.execute
             and module.execute.__globals__.get(function) is getattr(source, function))
    for dependency_name in ("ledger_source_guard", "inventory_scope"):
        _native_module("jifeng_erp." + dependency_name, _DEPENDENCY_SHAS[dependency_name])
    # Runtime Prepared flags are excluded: this channel neither trusts nor saves them.
    chain = tuple(_freeze({field: doc.get(field) for field in _DEFINITION_FIELDS}) for doc in target.chain)
    return kind, chain, module.execute


def resolve_report(name):
    target = resolve_target(name)
    if target is None:
        return None
    report = _native().get_report_doc(name)
    _require(frappe.session.user != "Guest" and report.name == target.canonical_name)
    definition = _definition(target)
    _require(_freeze({field: report.get(field) for field in _DEFINITION_FIELDS})
             == _freeze({field: target.chain[-1].get(field) for field in _DEFINITION_FIELDS}))
    saved_columns, saved_filters = [], []
    if len(target.chain) > 1 and target.chain[0].get("json"):
        saved = json.loads(target.chain[0].json)
        if saved:
            saved_columns, saved_filters = saved.get("columns"), saved.get("filters")
    _require(_freeze(report.get("custom_columns")) == _freeze(saved_columns)
             and _freeze(report.get("custom_filters")) == _freeze(saved_filters))
    return Resolved(target, report, definition)


def _options(report, custom_columns, is_tree, parent_field):
    return _freeze({"saved": deepcopy(report.get("custom_columns") or []),
                    "custom": deepcopy(custom_columns or []), "tree": bool(is_tree), "parent": parent_field})


class _ExtraRead:
    """Observe native custom-field semantics, including omitted/masked values."""
    def __init__(self, guard, doctype, field, names):
        self.guard, self.doctype, self.field = guard, doctype, field
        self.names = sorted(set(names))
        _require(isinstance(doctype, str) and bool(doctype) and isinstance(field, str) and bool(field)
                 and self.names and all(isinstance(name, str) and name for name in self.names))
        self.before = self._read(extend=True)

    def _raw(self):
        rows = frappe.get_all(self.doctype, filters={"name": ["in", self.names]}, fields=["*"], limit_page_length=0)
        parents = {}
        if frappe.get_meta(self.doctype).istable:
            for row in rows:
                _require(all(isinstance(row.get(field), str) and row.get(field)
                             for field in ("parent", "parenttype", "parentfield")))
                key = row["parenttype"], row["parent"]
                parents[key] = frappe.get_doc(*key)
        return rows, parents

    def _read(self, *, extend=False):
        rows, parents = self._raw()
        raw_before = (_freeze(sorted(rows, key=lambda row: row["name"])),
                      _freeze(sorted((key, _freeze(doc)) for key, doc in parents.items())))
        if extend:
            self.guard.include_doctypes({self.doctype, *[key[0] for key in parents]})
        _require(frappe.has_permission(self.doctype, "read"))
        # Exact native get_data_for_custom_field query: preserve its default
        # limit, omission of invisible identities, and native field masking.
        data = frappe._dict(frappe.get_list(self.doctype, filters={"name": ["in", self.names]},
                                          fields=["name", self.field], as_list=1))
        by_name = {row["name"]: row for row in rows}
        meta = frappe.get_meta(self.doctype)
        from frappe.model import get_permitted_fields
        mask = _native_module("frappe.model.utils.mask", _MASK_SHA, ("mask_field_value",))
        for name, value in data.items():
            _require(name in by_name)
            row = by_name[name]
            parenttype = row.get("parenttype") if meta.istable else None
            doc = frappe.get_doc(self.doctype, name)
            if parenttype:
                parent = parents[parenttype, row["parent"]]
                _require(parent.has_permission("read"))
                parent_meta = frappe.get_meta(parenttype)
                table = parent_meta.get_field(row["parentfield"])
                levels = set(parent_meta.get_permlevel_access("read", user=self.guard.user))
                if 0 not in levels and frappe.share.get_shared(parenttype, self.guard.user, rights=["read"], limit=1):
                    levels.add(0)
                _require(table and table.fieldtype == "Table" and table.options == self.doctype
                         and (table.permlevel or 0) in levels)
                _require(self.guard.user == "Administrator" or not table.get("mask")
                         or (table.permlevel or 0) in set(parent_meta.get_permlevel_access("mask", user=self.guard.user)))
                bound = next((child for child in parent.get(row["parentfield"]) or [] if child.name == name), None)
                _require(bound is not None and _freeze(bound) == _freeze(doc))
            else:
                _require(doc.has_permission("read"))
            permitted = get_permitted_fields(self.doctype, parenttype=parenttype, user=self.guard.user,
                                             permission_type="read", ignore_virtual=True)
            _require(self.field in permitted and self.field in meta.get_valid_columns())
            raw = doc.get(self.field)
            field = meta.get_field(self.field)
            needs_mask = (self.guard.user != "Administrator" and field and field.get("mask")
                          and (field.permlevel or 0) not in set(meta.get_permlevel_access("mask", parenttype=parenttype,
                                                                                       user=self.guard.user)))
            expected = mask.mask_field_value(field, raw) if needs_mask else raw
            _require(_freeze(value) == _freeze(expected))
        later_rows, later_parents = self._raw()
        raw_after = (_freeze(sorted(later_rows, key=lambda row: row["name"])),
                     _freeze(sorted((key, _freeze(doc)) for key, doc in later_parents.items())))
        _require(raw_before == raw_after)
        return data, raw_before

    def check(self):
        _require(_freeze(self._read()) == _freeze(self.before))

    def state(self):
        rows, parents = self._raw()
        raw = (_freeze(sorted(rows, key=lambda row: row["name"])),
               _freeze(sorted((key, _freeze(doc)) for key, doc in parents.items())))
        _require(raw == self.before[1])


def _custom_data(frame, columns, result):
    data = {}
    for column in columns:
        link = column.get("link_field")
        if not link:
            continue
        link = {"fieldname": link} if isinstance(link, str) else link
        names = list({row.get(link.get("fieldname")) for row in result if row.get(link.get("fieldname"))})
        if names:
            observation = _ExtraRead(frame["guard"], column.get("doctype"), column.get("fieldname"), names)
            frame["guard"].add_output_observer(observation)
            data[column.get("doctype"), column.get("fieldname")] = observation.before[0]
    return data


def _add_custom_column_data(frame, custom_columns, result):
    conflicts = []
    for column in custom_columns:
        if len(column["fieldname"].split("-")) > 1:
            conflicts.append(frappe.unscrub(column["fieldname"].split("-")[1]))
        column["fieldname"] = column["fieldname"].split("-")[0]
    pending = custom_columns
    while pending:
        data = _custom_data(frame, pending, result)
        unresolved = []
        for column in pending:
            key = column.get("doctype"), column.get("fieldname")
            if key not in data:
                unresolved.append(column)
                continue
            for row in result:
                link = column.get("link_field")
                link = {"fieldname": link} if isinstance(link, str) else link
                reference = row.get(link.get("fieldname"))
                if not reference:
                    continue
                if key[0] in conflicts:
                    column["fieldname"] = column.get("id")
                row[column.get("fieldname")] = data.get(key).get(reference)
        if len(unresolved) == len(pending):
            break
        pending = unresolved
    return result


def execute_script_report(report, filters):
    """Only the owned fresh frame can dispatch a five-report controller body."""
    frame = _FRAME.get()
    _require(frame is not None and report is frame["resolved"].report
             and _freeze(filters or {}) == _freeze(frame["filters"] or {}))
    _require(_definition(resolve_target(frame["name"])) == frame["resolved"].definition)
    start = monotonic()
    result = frame["resolved"].definition[2](frappe._dict(deepcopy(filters or {})))
    frappe.cache.hset("report_execution_time", report.name, monotonic() - start)
    return result


def _generate(frame, columns, is_tree, parent_field):
    native, report = _native(), frame["resolved"].report
    _require(_options(report, columns, is_tree, parent_field) == frame["options"])
    raw = native.get_report_result(report, frame["filters"]) or []
    _require(frame["binding"]["uses"] == 1)
    out_columns, result, message, chart, summary, skip_total = native.ljust_list(raw, 6)
    out_columns = [native.get_column_as_dict(col) for col in (out_columns or [])]
    original = [col["fieldname"] for col in out_columns]
    result = native.normalize_result(result, out_columns)
    if report.get("custom_columns"):
        out_columns = [deepcopy(col) for col in report.custom_columns if col.get("link_field") or col["fieldname"] in original]
    if columns:
        for col in deepcopy(columns):
            out_columns.insert(col["insert_after_index"] + 1, col)
    extras = [col for col in out_columns if col["fieldname"] not in original]
    if extras:
        result = _add_custom_column_data(frame, extras, result)
    if result:
        result = native.get_filtered_data(report.ref_doctype, out_columns, result, frame["user"])
    has_total = native.cint(report.add_total_row) and result and not skip_total
    if has_total:
        result = native.add_total_row(result, out_columns, is_tree=is_tree, parent_field=parent_field)
    if isinstance(frame["filters"], dict) and frame["filters"].get("translate_data"):
        result = native.translate_report_data(result, has_total)
    return {"result": result, "columns": out_columns, "message": message, "chart": chart,
            "report_summary": summary, "skip_total_row": skip_total or 0, "status": None,
            "execution_time": frappe.cache.hget("report_execution_time", report.name) or 0}


def generate_fresh(report_name, filters=None, user=None, custom_columns=None, is_tree=False, parent_field=None,
                   *, are_default_filters=True, js_filters=None, transform=None):
    """A formatter runs before the final guard; no persisted cache is consumed."""
    _require(_FRAME.get() is None and (user is None or user == frappe.session.user))
    native, actor = _native(), frappe.session.user
    native.validate_filters_permissions(report_name, filters, actor, js_filters)
    resolved = resolve_report(report_name)
    _require(resolved is not None)
    report = resolved.report
    selected = deepcopy(report.custom_filters) if native.sbool(are_default_filters) and report.get("custom_filters") else deepcopy(filters or {})
    selected = json.loads(selected) if isinstance(selected, str) and selected else selected
    if isinstance(selected, dict):
        _require(not selected.get("prepared_report_name"))
    columns = frappe.parse_json(custom_columns) if custom_columns else None
    guard = LedgerSourceGuard(resolved.target.kind)
    frame = {"name": report_name, "user": actor, "resolved": resolved, "filters": selected,
             "guard": guard, "options": _options(report, columns, is_tree, parent_field)}
    token = _FRAME.set(frame)
    try:
        with output_guard(guard) as binding:
            frame["binding"] = binding
            payload = _generate(frame, columns, is_tree, parent_field)
            payload["add_total_row"] = report.add_total_row and not payload.get("skip_total_row", False)
            if native.sbool(are_default_filters) and report.get("custom_filters"):
                payload["custom_filters"] = deepcopy(report.custom_filters)
            value = transform(payload) if transform else payload
            _require(frappe.session.user == actor and _options(report, columns, is_tree, parent_field) == frame["options"])
            final = resolve_report(report_name)
            _require(final is not None and final.definition == resolved.definition)
            guard.finish()
            return value
    finally:
        _FRAME.reset(token)


def run(report_name, filters=None, user=None, ignore_prepared_report=False, custom_columns=None,
        is_tree=False, parent_field=None, are_default_filters=True, js_filters=None):
    # Native whitelisted run keeps user for compatibility but uses this session.
    return generate_fresh(report_name, filters, custom_columns=custom_columns, is_tree=is_tree,
                          parent_field=parent_field, are_default_filters=are_default_filters, js_filters=js_filters)


def run_query_report(report, filters=None, user=None, ignore_prepared_report=False, are_default_filters=True):
    target = resolve_target(report.name)
    _require(target is not None and _freeze({field: report.get(field) for field in _DEFINITION_FIELDS})
             == _freeze({field: target.chain[0].get(field) for field in _DEFINITION_FIELDS}))
    payload = generate_fresh(report.name, filters, user=user, are_default_filters=are_default_filters)
    columns = []
    for item in payload.get("columns"):
        if isinstance(item, dict):
            column = frappe._dict(item)
            if not column.fieldname:
                column.fieldname = column.label
            columns.append(column)
        else:
            fieldtype, options = "Data", None
            parts = item.split(":")
            if len(parts) > 1 and parts[1]:
                fieldtype = parts[1]
                if "/" in fieldtype:
                    fieldtype, options = fieldtype.split("/")
            columns.append(frappe._dict(label=parts[0], fieldtype=fieldtype, fieldname=parts[0], options=options))
    return columns, list(payload.get("result"))
