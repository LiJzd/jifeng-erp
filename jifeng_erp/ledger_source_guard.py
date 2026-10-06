"""Single report source observations; no permission bypass, global patch or cache.

Only callers passing this object opt in. Full native row/Company gates remain
in their original helpers. This guard rejects incomplete read/mask projections
and changes observed while producing the report; it is not a transaction lock.
"""

from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar

import frappe
from frappe.model import get_permitted_fields
from frappe.query_builder.utils import mask_fields


IDENTITY = {"name", "modified", "docstatus"}
CHILD_IDENTITY = {"name", "parent", "parenttype", "parentfield", "idx"}
_OUTPUT_GUARD = ContextVar("jifeng_ledger_output_guard", default=None)


def ledger_guard(kind):
    """Use the one owned output guard, or retain direct source-call behavior."""
    binding = _OUTPUT_GUARD.get()
    if binding is None:
        return LedgerSourceGuard(kind)
    if binding["guard"].kind != kind or binding["guard"].user != frappe.session.user:
        _deny()
    binding["uses"] += 1
    if binding["uses"] != 1:
        _deny()
    return binding["guard"]


@contextmanager
def output_guard(guard):
    """Internal execution binding; no flags or caller identity grant a read."""
    if (_OUTPUT_GUARD.get() is not None or not isinstance(guard, LedgerSourceGuard)
            or guard.user != frappe.session.user):
        _deny()
    binding = {"guard": guard, "uses": 0}
    token = _OUTPUT_GUARD.set(binding)
    try:
        yield binding
    finally:
        _OUTPUT_GUARD.reset(token)


def spec(fields, tables=None, optional=()):
    return {"fields": IDENTITY | set(fields), "tables": tables or {}, "optional": set(optional)}


def table(doctype, fields, optional=()):
    return (doctype, CHILD_IDENTITY | set(fields), set(optional))


STOCK_ROW = {"item_code", "stock_uom", "qty", "stock_qty", "conversion_factor", "jf_flow_order"}
PR_QUANTITY = STOCK_ROW | {"rejected_qty", "warehouse", "rejected_warehouse", "purchase_receipt_item",
                           "return_qty_from_rejected_warehouse"}
REQUEST_LINK = {"material_request", "material_request_item"}
ORDER_LINK = {"purchase_order", "purchase_order_item"}
SCR_ROW = {"subcontracting_order", "subcontracting_order_item", "subcontracting_receipt_item", "item_code",
           "item_name", "stock_uom", "qty", "rejected_qty", "process_loss_qty", "conversion_factor",
           "warehouse", "rejected_warehouse", "quality_inspection", "jf_flow_order", "secondary_item_type"}
SCO_ROW = {"item_code", "item_name", "stock_uom", "qty", "conversion_factor", "jf_flow_order"}


def dependencies(kind):
    """Only source fields actually consumed by this report or its integrity gates."""
    shared = {"Company": spec({"name"}), "Supplier": spec({"name"})}
    if kind in ("purchase_request", "purchase_receipt"):
        shared.update({
            "Item": spec({"stock_uom", "is_stock_item"}),
            "Warehouse": spec({"company", "is_group"}),
            "Jifeng Flow Order": spec({"company", "item_code"},
                                      {"materials": table("Jifeng Material Line", {"item_code", "uom"})}),
            "Purchase Receipt": spec({"company", "supplier", "currency", "conversion_rate", "is_return", "return_against"},
                                     {"items": table("Purchase Receipt Item", PR_QUANTITY)}),
        })
        if kind == "purchase_request":
            shared["Material Request"] = spec({"company", "material_request_type", "transaction_date", "status"},
                {"items": table("Material Request Item", STOCK_ROW | {"item_name", "warehouse", "schedule_date"})})
            shared["Purchase Order"] = spec({"company", "supplier", "status"},
                {"items": table("Purchase Order Item", STOCK_ROW | REQUEST_LINK)})
            shared["Purchase Receipt"]["tables"]["items"] = table("Purchase Receipt Item", PR_QUANTITY | REQUEST_LINK | ORDER_LINK)
        else:
            shared["Purchase Receipt"]["fields"].update({"supplier_name", "posting_date", "posting_time"})
            shared["Purchase Receipt"]["tables"]["items"] = table("Purchase Receipt Item", PR_QUANTITY |
                {"item_name", "amount", "base_amount", "net_amount", "base_net_amount"})
        return shared
    if kind not in ("subcontracting_progress", "subcontracting_receipt", "subcontracting_material"):
        raise ValueError("unknown ledger guard kind")
    shared["Item"] = spec({"name"})
    shared["Jifeng Flow Order"] = spec({"company"})
    shared["Subcontracting Order"] = spec({"company", "supplier", "supplier_name", "transaction_date", "status"},
                                         {"items": table("Subcontracting Order Item", SCO_ROW)})
    if kind == "subcontracting_progress":
        shared["Subcontracting Order"]["tables"]["items"] = table("Subcontracting Order Item", SCO_ROW,
                                                                   {"expected_delivery_date", "schedule_date"})
    if kind != "subcontracting_material":
        shared["Subcontracting Receipt"] = spec({"company", "supplier", "supplier_name", "posting_date", "is_return", "return_against"},
                                               {"items": table("Subcontracting Receipt Item", SCR_ROW)})
    else:
        # Material movements use Order Item identity/item/unit/Flow, not plan quantities.
        shared["Subcontracting Order"]["tables"]["items"] = table("Subcontracting Order Item",
                                                               {"item_code", "item_name", "jf_flow_order"})
        shared["Subcontracting Order"]["tables"]["supplied_items"] = table("Subcontracting Order Supplied Item",
            {"reference_name", "main_item_code", "rm_item_code", "stock_uom"})
        shared["Stock Entry"] = spec({"company", "posting_date", "posting_time", "purpose", "is_return", "subcontracting_order"},
            {"items": table("Stock Entry Detail", {"sco_rm_detail", "subcontracted_item", "item_code", "item_name",
                        "stock_uom", "jf_flow_order", "transfer_qty", "s_warehouse", "t_warehouse"})}, {"supplier"})
    return shared


def _freeze(value):
    if callable(getattr(value, "as_dict", None)):
        value = value.as_dict()
    if isinstance(value, dict):
        return tuple((key, _freeze(item)) for key, item in sorted(value.items()) if not key.startswith("__"))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return type(value).__name__, repr(value)


def _query_state(rows):
    return tuple(sorted((_freeze(row) for row in rows), key=repr))


def _deny():
    frappe.throw("无法完整读取报表来源字段，或查询期间来源及权限已改变，请重新查询", frappe.PermissionError)


class LedgerSourceGuard:
    def __init__(self, kind):
        self.specs = dependencies(kind)
        self.kind = kind
        self.user = frappe.session.user
        self.documents, self.queries = {}, []
        self.output_observers = []
        self.doctypes = set(self.specs)
        for value in self.specs.values():
            self.doctypes.update(child[0] for child in value["tables"].values())
        self.report_name = {"purchase_request": "Jifeng Purchase Request Ledger",
                            "purchase_receipt": "Jifeng Purchase Receipt Ledger",
                            "subcontracting_progress": "Jifeng Subcontracting Progress",
                            "subcontracting_receipt": "Jifeng Subcontracting Receipt Ledger",
                            "subcontracting_material": "Jifeng Subcontracting Material Ledger"}[kind]
        if kind in ("purchase_request", "purchase_receipt"):
            self.doctypes.add("UOM Conversion Detail")
        self.authorization_before = self._authorization()

    def include_doctypes(self, doctypes):
        """Observe extra-column schemas before their first native field read."""
        if frappe.session.user != self.user or self._authorization() != self.authorization_before:
            _deny()
        if any(not isinstance(name, str) or not name for name in doctypes):
            _deny()
        previous = set(self.doctypes)
        enlarged = previous | set(doctypes)
        extended = self._authorization(enlarged)
        if self._authorization(previous) != self.authorization_before:
            _deny()
        self.doctypes = enlarged
        self.authorization_before = extended
        if self._authorization() != extended:
            _deny()

    def add_output_observer(self, observer):
        self.output_observers.append(observer)

    def _authorization(self, doctypes=None):
        # Internal raw observations never grant access or return hidden rows.
        # Snapshot rows, not native request caches, to detect late cached allows.
        rows = []
        doctypes = self.doctypes if doctypes is None else doctypes
        for doctype, filters in (
            ("User", {"name": self.user}),
            ("Has Role", {"parenttype": "User", "parent": self.user}),
            ("User Permission", {"user": self.user}),
            ("Report", {"name": self.report_name}),
            ("Has Role", {"parenttype": "Report", "parent": self.report_name}),
            ("Custom Role", {"report": self.report_name}),
            ("DocPerm", {"parent": ["in", sorted(doctypes)]}),
            ("Custom DocPerm", {"parent": ["in", sorted(doctypes)]}),
            ("DocShare", {"share_doctype": ["in", sorted(doctypes)]}),
            ("DocType", {"name": ["in", sorted(doctypes)]}),
            ("DocField", {"parent": ["in", sorted(doctypes)]}),
            ("Custom Field", {"dt": ["in", sorted(doctypes)]}),
            ("Property Setter", {"doc_type": ["in", sorted(doctypes)]}),
        ):
            result = frappe.get_all(doctype, filters=filters, fields=["*"], limit_page_length=0)
            rows.append((doctype, tuple(sorted((_freeze(row) for row in result), key=repr))))
            if doctype == "Custom Role":
                names = [row["name"] for row in result]
                roles = frappe.get_all("Has Role", filters={"parenttype": "Custom Role", "parent": ["in", names]},
                                       fields=["*"], limit_page_length=0) if names else []
                rows.append(("Custom Role roles", _query_state(roles)))
        return self.user, _freeze(rows)

    def query(self, method, doctype, **kwargs):
        copied = deepcopy(kwargs)
        # Raw membership detects newly hidden/deleted/inserted matching sources;
        # native visible membership and original complete rules still decide reads.
        raw = frappe.get_all(doctype, **deepcopy(copied))
        result = raw if method == "get_all" else frappe.get_list(doctype, **deepcopy(copied))
        self.queries.append((method, doctype, copied, _query_state(raw), _query_state(result)))
        return result

    def _fields(self, doctype, names, parenttype=None, permission="read"):
        meta = frappe.get_meta(doctype)
        # Company keeps its native select document gate. A consumed extra field
        # must still be exposed by native select/search or native read fields;
        # loading the full Company does not authorize its default_currency.
        permitted = set(get_permitted_fields(doctype, parenttype=parenttype, user=self.user,
                        permission_type=permission, ignore_virtual=True))
        if permission == "select" and not set(names).issubset(permitted):
            permitted.update(get_permitted_fields(doctype, parenttype=parenttype, user=self.user,
                             permission_type="read", ignore_virtual=True))
        valid = set(meta.get_valid_columns())
        if not set(names).issubset(valid) or not set(names).issubset(permitted):
            _deny()
        levels = set(meta.get_permlevel_access("mask", parenttype=parenttype, user=self.user))
        if self.user != "Administrator" and any((field := meta.get_field(name)) and field.get("mask")
                and (field.permlevel or 0) not in levels for name in names):
            _deny()
        return meta

    def _check(self, document, value, permission):
        doctype = document.doctype
        if permission not in ("read", "select") or permission == "select" and doctype != "Company":
            _deny()
        if any(not frappe.get_meta(doctype).get_field(name) and document.get(name) not in (None, "")
               for name in value["optional"]):
            _deny()
        names = value["fields"] | {name for name in value["optional"] if frappe.get_meta(doctype).get_field(name)}
        meta = self._fields(doctype, names, permission=permission)
        projections = [{name: document.get(name) for name in names}]
        if _freeze(mask_fields(doctype, sorted(names), deepcopy(projections), as_dict=True)) != _freeze(projections):
            _deny()
        read_levels = set(meta.get_permlevel_access("read", user=self.user))
        # Match native get_permitted_fieldnames' base-level shared-document rule.
        if 0 not in read_levels and frappe.share.get_shared(doctype, self.user, rights=["read"], limit=1):
            read_levels.add(0)
        mask_levels = set(meta.get_permlevel_access("mask", user=self.user))
        for fieldname, (childtype, required, optional) in value["tables"].items():
            field = meta.get_field(fieldname)
            if (not field or field.fieldtype != "Table" or field.options != childtype
                    or (field.permlevel or 0) not in read_levels
                    or self.user != "Administrator" and field.get("mask") and (field.permlevel or 0) not in mask_levels):
                _deny()
            child_names = required | {name for name in optional if frappe.get_meta(childtype).get_field(name)}
            self._fields(childtype, child_names, parenttype=doctype)
            projected = []
            for row in document.get(fieldname) or []:
                if (row.doctype != childtype or row.parent != document.name or row.parenttype != doctype
                        or row.parentfield != fieldname):
                    _deny()
                if any(not frappe.get_meta(childtype).get_field(name) and row.get(name) not in (None, "")
                       for name in optional):
                    _deny()
                projected.append({name: row.get(name) for name in child_names})
            if _freeze(mask_fields(childtype, sorted(child_names), deepcopy(projected), as_dict=True,
                                  parent_doctype=doctype)) != _freeze(projected):
                _deny()

    def document(self, document, permission="read", extra=(), tables=None):
        key = document.doctype, document.name
        if key[0] not in self.specs:
            _deny()
        before = _freeze(document)
        value = deepcopy(self.specs[key[0]])
        value["fields"].update(extra)
        value["tables"].update(tables or {})
        if tables:
            # New dependency schemas require their own baseline metadata snapshot;
            # these are declared at construction, before callbacks can change them.
            if any(child[0] not in self.doctypes for child in tables.values()):
                _deny()
        self._check(document, value, permission)
        if key in self.documents:
            if before != self.documents[key][1]:
                _deny()
            self.documents[key][2]["fields"].update(extra)
            self.documents[key][2]["tables"].update(tables or {})
        else:
            self.documents[key] = [document, before, value, permission]
        return document

    def finish(self):
        # No new authorization result is reused. Re-run every native row/field
        # gate and membership observation; then inspect states after callbacks.
        for _pass in range(2):
            if frappe.session.user != self.user or self._authorization() != self.authorization_before:
                _deny()
            for method, doctype, kwargs, raw, visible in self.queries:
                if _query_state(frappe.get_all(doctype, **deepcopy(kwargs))) != raw:
                    _deny()
                if method == "get_list" and _query_state(frappe.get_list(doctype, **deepcopy(kwargs))) != visible:
                    _deny()
            for observer in self.output_observers:
                observer.check()
            for (doctype, name), (held, before, value, permission) in self.documents.items():
                fresh = frappe.get_doc(doctype, name)
                if _freeze(held) != before or _freeze(fresh) != before or not fresh.has_permission(permission):
                    _deny()
                self._check(fresh, value, permission)
                if _freeze(fresh) != before:
                    _deny()
        # Last native permission/mask callback can mutate any earlier source.
        # Final observations contain no permission callback that could borrow allow.
        for (doctype, name), (held, before, _value, _permission) in self.documents.items():
            if _freeze(held) != before or _freeze(frappe.get_doc(doctype, name)) != before:
                _deny()
        for _method, doctype, kwargs, raw, _visible in self.queries:
            if _query_state(frappe.get_all(doctype, **deepcopy(kwargs))) != raw:
                _deny()
        for observer in self.output_observers:
            observer.state()
        if frappe.session.user != self.user or self._authorization() != self.authorization_before:
            _deny()
