"""Offline fixtures for the frozen candidate; never imports native Frappe.

Business values remain owned by each original test. Only schema/Document APIs,
standard parent/child metadata and explicitly named lookup fixtures are added.
"""
import importlib
import importlib.util
import pathlib
import sys
import types
from copy import deepcopy
from unittest.mock import Mock, patch

CANDIDATE = pathlib.Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("frozen_ledger_fixture_types", CANDIDATE / "tests/test_ledger_field_mask_candidate.py")
models = importlib.util.module_from_spec(spec)
spec.loader.exec_module(models)
Row, Doc, Environment = models.Row, models.Doc, models.Environment


def plain(value):
    if isinstance(value, dict): return {key: plain(item) for key, item in dict.items(value)}
    if isinstance(value, list): return [plain(item) for item in value]
    if isinstance(value, tuple): return tuple(plain(item) for item in value)
    return deepcopy(value)


class FixtureDoc(Doc):
    def __init__(self, env, precision_callback=None, **values):
        super().__init__(env, **values)
        object.__setattr__(self, "precision_callback", precision_callback)

    def precision(self, field):
        return self.precision_callback(field) if self.precision_callback else 6

    def __deepcopy__(self, memo):
        return FixtureDoc(self.env, precision_callback=self.precision_callback,
                          **{key: deepcopy(value, memo) for key, value in self.items_dict()})


class FixtureEnvironment(Environment):
    def __init__(self, case, provider):
        super().__init__()
        self.case, self.provider = case, provider
        self.additional = {}
        self.frappe.get_doc = self.document

    def match(self, value, rule):
        if isinstance(rule, (list, tuple)):
            operator, expected = rule
            if operator == ">": return value is not None and value > expected
            if operator == "<": return value is not None and value < expected
            if operator == "!=": return value != expected
        return super().match(value, rule)

    def convert(self, doctype, raw):
        values = dict(raw) if isinstance(raw, dict) else vars(raw)
        result = {field: [] if meta.fieldtype == "Table" else None for field, meta in self.meta[doctype].fields.items()}
        result.update(plain(values))
        # These legacy arithmetic fixtures represent submitted native Documents.
        # Never replace a supplied status, reference, unit, quantity or identity.
        result.setdefault("doctype", doctype)
        result["docstatus"] = values.get("docstatus", 1)
        result["modified"] = values.get("modified", "2026-10-06 00:00:00")
        for table, meta in self.meta[doctype].fields.items():
            if meta.fieldtype != "Table": continue
            children = []
            for index, row in enumerate(values.get(table) or [], 1):
                fields = dict(row) if isinstance(row, dict) else vars(row)
                data = {key: None for key in self.meta[meta.options].fields}
                data.update(plain(fields))
                for key, value in dict(doctype=meta.options, name=f"{values['name']}:{table}:{index}",
                        parent=values["name"], parenttype=doctype, parentfield=table, idx=index,
                        docstatus=result["docstatus"]).items():
                    if key not in fields: data[key] = value
                children.append(FixtureDoc(self, precision_callback=getattr(row, "precision", None), **data))
            result[table] = children
        return FixtureDoc(self, precision_callback=getattr(raw, "precision", None), **result)

    def refresh(self):
        sources = {**self.additional}
        for doctype, documents in self.provider().items():
            sources[doctype] = {**sources.get(doctype, {}), **documents}
        self.data = {(dt, name): self.convert(dt, doc) for dt, docs in sources.items() for name, doc in docs.items()}
        self.hidden = getattr(self.case, "hidden", self.hidden)
        self.denied = {(dt, name, "read") for dt, name in getattr(self.case, "full_denied", set())}

    def document(self, doctype, name):
        self.refresh()
        return deepcopy(self.data[doctype, name])

    def records(self, doctype):
        self.refresh()
        return super().records(doctype)


def install(case, provider, company, supplier, flows):
    env = FixtureEnvironment(case, provider)
    model = types.ModuleType("frappe.model"); model.get_permitted_fields = env.permitted
    query = types.ModuleType("frappe.query_builder.utils"); query.mask_fields = env.mask
    utils = types.ModuleType("frappe.utils")
    utils.cint = lambda value: int(value or 0)
    utils.getdate = lambda value: value
    utils.today = lambda: "2026-09-30"
    utils.date_diff = lambda *args: 0
    refs = types.ModuleType("jifeng_erp.sample_source_queries")
    refs.check_protected_reference = Mock(return_value=False)
    package = types.ModuleType("jifeng_erp"); package.__path__ = [str(CANDIDATE / "jifeng_erp")]
    patches = patch.dict(sys.modules, {"frappe": env.frappe, "frappe.model": model,
            "frappe.query_builder.utils": query, "frappe.utils": utils,
            "jifeng_erp": package, "jifeng_erp.sample_source_queries": refs})
    patches.start(); case.addCleanup(patches.stop)
    for name in ("ledger_source_guard", "inventory_scope", "procurement_reports", "subcontracting", "subcontracting_material"):
        sys.modules.pop("jifeng_erp." + name, None)
    guard = importlib.import_module("jifeng_erp.ledger_source_guard")
    env.schemas(guard)
    env.meta["Company"].fields["default_currency"] = Row(fieldname="default_currency", fieldtype="Link", permlevel=0, mask=0)
    env.additional = {"Company": {company: Row(name=company, default_currency="CNY")},
                      "Supplier": {supplier: Row(name=supplier)},
                      "Jifeng Flow Order": {name: Row(name=name, company=company) for name in flows}}
    scope = importlib.import_module("jifeng_erp.inventory_scope")
    proc = importlib.import_module("jifeng_erp.procurement_reports")
    sub = importlib.import_module("jifeng_erp.subcontracting")
    material = importlib.import_module("jifeng_erp.subcontracting_material")
    case.env, case.guard, case.scope, case.frappe = env, guard, scope, env.frappe
    return proc, sub, material


def procurement(case):
    proc, _sub, _material = install(case, lambda: case.documents, "JF", "SUP", ("FLOW", "OTHER"))
    case.module = proc
    # Explicit existing lookup fixtures required by the old filter assertions.
    case.env.additional["Supplier"]["OTHER"] = Row(name="OTHER")
    case.env.additional["Warehouse"] = {"GROUP": Row(name="GROUP", company="JF", is_group=1, lft=0, rgt=5)}
    for index, name in enumerate(("MAIN", "REJECT"), 1):
        case.documents["Warehouse"][name].update(lft=index, rgt=index)
    # Keep original introspection assertions; side effects are actual candidate functions.
    case.module.readable_documents = Mock(side_effect=case.scope.readable_documents)
    return case.module


def subcontracting(case):
    case.original.docstatus = case.return_doc.docstatus = 1
    sources = lambda: {"Subcontracting Receipt": {"SCR": case.original, "RETURN": case.return_doc},
        "Subcontracting Order": {"SCO": Row(name="SCO", company="JF", supplier="SUP", supplier_name="Supplier",
                         transaction_date="2026-09-30", status="Open", items=list(case.order.values()))}}
    _proc, sub, _material = install(case, sources, "JF", "SUP", ("FLOW",))
    case.report = sub


def observed_documents(case, documents, guard):
    """Used only at original mocked helper boundaries; no mock guard or finish."""
    result = {}
    for name, raw in documents.items():
        dt = "Subcontracting Receipt" if hasattr(raw, "is_return") else "Subcontracting Order"
        doc = case.env.document(dt, name)
        if guard: guard.document(doc)
        result[name] = doc
    return result


def subcontracting_ledger(case, selected=None):
    # Preserve the original selected-output fixture and full historical closure.
    # Native parent, field and finish gates use the actual candidate helper.
    selected = selected if selected is not None else {"SCR": case.original, "RETURN": case.return_doc}
    filters = Row(company="JF", from_date="2026-09-01", to_date="2026-09-30")
    source_readable = case.report._readable_documents
    def readable(doctype, query, complete=False, *, guard=None):
        if doctype == "Subcontracting Receipt" and isinstance(query, dict) and "posting_date" in query:
            result = observed_documents(case, selected, guard)
            if guard: guard.query("get_all", doctype, filters={"name": ["in", list(selected)]}, pluck="name", limit_page_length=0)
            return result
        return source_readable(doctype, query, complete=complete, guard=guard)
    with patch.object(case.report, "_readable_documents", side_effect=readable):
        return case.report.subcontracting_receipt_ledger(filters)[1]


def material(case):
    case.order.setdefault("docstatus", 1)
    case.order.setdefault("transaction_date", "2026-09-30")
    case.order.setdefault("status", "Open")
    provider = lambda: {"Subcontracting Order": case.orders, "Stock Entry": {case.entry.name: case.entry}}
    _proc, _sub, module = install(case, provider, "公司", "供应商", ("FLOW-1",))
    case.module = module
    # Original API tests inspect these mocks. Each forwards to the actual candidate
    # implementation, so all parent/child/query/finish checks are exercised.
    for name in ("_scope", "_orders", "_order_items", "_readable_documents"):
        setattr(module, name, Mock(wraps=getattr(module, name)))
