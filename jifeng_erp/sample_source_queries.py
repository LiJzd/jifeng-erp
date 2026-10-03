"""Bounded deny-only guards for native linked documents and our report references.

Native linked_with counts use get_all before permission filtering. Re-read only
the same covered candidates and subtract unreadable sample-derived documents,
including their join multiplicity; ordinary hidden counts remain native. This
does not authorize SQL/Script/Prepared Reports or arbitrary trusted get_all.
"""

import frappe
from frappe.desk.form import linked_with as native

from jifeng_erp import sample_source_permissions as sources


CHILDREN = frozenset({
    "Jifeng Sample Order Item", "Quotation Item", "Sales Order Item",
    "Pricing Rule Detail", "Sales Taxes and Charges", "Payment Schedule",
    "Quotation Lost Reason Detail", "Packed Item", "Competitor Detail",
    "Item Wise Tax Detail", "Sales Team", "Work Order Item", "Work Order Operation",
    "Work Order Additional Item", "Jifeng Material Line", "Jifeng Operation Line",
})
COVERED = sources.PARENTS | CHILDREN
_DENIED = "无法完整读取关联的样品及其来源，请核对来源读取权限"
_CHANGED = "关联数据已变化，请重新读取"


class _ChangedReference(Exception):
    pass


def _deny(message=_DENIED):
    frappe.throw(message, frappe.PermissionError)


def _subject(doctype, name, *, required=False):
    if not name or doctype not in COVERED:
        return None
    doc = sources._reference(doctype, name)
    if doc is None:
        if required:
            raise _ChangedReference
        # A report's existing ordinary dangling link does not gain a new rule.
        return None
    parent = doc if doctype in sources.PARENTS else sources._child_parent(doc)
    return parent if sources._scope(parent) else None


def _check_reference(doctype, name, company=None, *, required=False):
    subject = _subject(doctype, name, required=required)
    if subject is None:
        return False
    if company is not None and subject.get("company") != company:
        _deny()
    sources._check(subject, frappe.session.user)
    return True


def check_protected_reference(doctype, name, company=None):
    """Internal report guard: refuse the whole report, never omit its movements."""
    try:
        return _check_reference(doctype, name, company)
    except (frappe.PermissionError, frappe.DoesNotExistError, frappe.ValidationError,
            ValueError, TypeError, KeyError):
        _deny()


def _matching_filters(doctype, name, linked_doctype, context):
    """Mirror Frappe 16 get_linked_docs' exact native filter branches.

    Context comes only from get_linked_doctypes, never a caller override. No
    deduplication here: native raw counts include child join multiplicity.
    """
    meta = frappe.get_meta(linked_doctype)
    if meta.issingle:
        return None
    filters, or_filters = [], []
    if filters_ctx := context.get("filters"):
        filters = filters_ctx
    elif context.get("get_parent"):
        if not frappe.get_meta(doctype).istable:
            return None
        parent = frappe.db.get_value(doctype, name, ["parenttype", "parent"],
                                     as_dict=True, order_by=None)
        if not parent or parent.parenttype != linked_doctype:
            return None
        filters = [[linked_doctype, "name", "=", parent.parent]]
    elif child_doctype := context.get("child_doctype"):
        links = context.get("child_links") or [
            {"child_doctype": child_doctype, "fieldname": context["fieldname"]},
        ]
        or_filters = [[link["child_doctype"], field, "=", name]
                      for link in links for field in link["fieldname"]]
        if field := context.get("doctype_fieldname"):
            filters.append([child_doctype, field, "=", doctype])
    elif fields := context.get("fieldname"):
        if isinstance(fields, str):
            fields = [fields]
        or_filters = [[linked_doctype, field, "=", name] for field in fields]
        if field := context.get("doctype_fieldname"):
            filters.append([linked_doctype, field, "=", doctype])
        if meta.istable and not (
            frappe.db.exists("DocField", {"options": linked_doctype})
            or frappe.db.exists(linked_doctype, {"parenttype": doctype, "parent": name})
        ):
            return None
    return filters, or_filters


def _unreadable_names(doctype, names):
    denied = set()
    for name in dict.fromkeys(names):
        try:
            _check_reference(doctype, name, required=True)
        except (frappe.PermissionError, frappe.DoesNotExistError, frappe.ValidationError,
                ValueError, TypeError, KeyError):
            denied.add(name)
    return denied


@frappe.whitelist()
def get(doctype: str, docname: str):
    frappe.has_permission(doctype, doc=docname, throw=True)
    check_protected_reference(doctype, docname)
    linkinfo = native.get_linked_doctypes(doctype=doctype)
    result = native.get_linked_docs(doctype=doctype, name=docname, linkinfo=linkinfo)
    for linked_doctype, bucket in list(result.items()):
        if linked_doctype not in COVERED:
            continue
        context = linkinfo.get(linked_doctype)
        matched = _matching_filters(doctype, docname, linked_doctype, context) if context is not None else None
        if matched is None:
            _deny(_CHANGED)
        filters, or_filters = matched
        raw = frappe.get_all(linked_doctype, filters=filters, or_filters=or_filters,
                             fields=["name"], order_by=None)
        names = [row["name"] for row in raw]
        docs, hidden = bucket["docs"], bucket["hidden_count"]
        if len(names) != hidden + len(docs) or not all(doc["name"] in names for doc in docs):
            _deny(_CHANGED)
        try:
            denied = _unreadable_names(linked_doctype, names)
        except _ChangedReference:
            _deny(_CHANGED)
        visible = [doc for doc in docs if doc["name"] not in denied]
        # Remove the whole native payload, including amounts/add_fields. Preserve
        # ordinary raw duplicates and native pagination hidden_count behavior.
        hidden -= sum(name in denied for name in names) - (len(docs) - len(visible))
        if hidden < 0:
            _deny(_CHANGED)
        if visible or hidden:
            result[linked_doctype] = {**bucket, "docs": visible, "hidden_count": hidden}
        else:
            del result[linked_doctype]
    return result


@frappe.whitelist()
def get_submitted_linked_docs(doctype: str, name: str,
                             ignore_doctypes_on_cancel_all: str | list[str] | None = None):
    result = native.get_submitted_linked_docs(
        doctype=doctype, name=name, ignore_doctypes_on_cancel_all=ignore_doctypes_on_cancel_all,
    )
    check_protected_reference(doctype, name)
    for doc in result["docs"]:
        try:
            _check_reference(doc["doctype"], doc["name"], required=True)
        except (frappe.PermissionError, frappe.DoesNotExistError, frappe.ValidationError,
                ValueError, TypeError, KeyError, _ChangedReference):
            _deny()
    return result
