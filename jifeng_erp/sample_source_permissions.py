"""Deny-only sample source permissions under an explicit no-independent-share invariant.

Frappe 16 permits DocShare to override both has_permission and permission query
conditions. These hooks are sufficient only while sample-derived documents and
their children have no independent shares. Deployment must prove that invariant;
before_validate_source_binding and before_validate_docshare serialize subsequent
binding/sharing on the same existing parent. They never remove existing shares.

PQC filters before native pagination/count/aggregate/export. Native child queries
use the parent PQC, not the child's: reject ambiguous stored child identities at
the parent as well as checking an exact child parent/type/field on direct reads.
Link joins use target PQC; child joins rely on the fully checked parent. The
optional controller mixin is a final document check after native permission.

This is NOT a global SQL boundary. SQL/Script Reports, linked_with.hidden_count,
trusted get_all/ignore_permissions, and unknown custom parents/joins require
separate audits. Nor can these hooks repair shares inserted through direct SQL.
No request-global permission cache or client-supplied bypass flag is used.
"""

from contextvars import ContextVar

import frappe


SAMPLE = "Jifeng Sample Order"
SAMPLE_ITEM = "Jifeng Sample Order Item"
FLOW = "Jifeng Flow Order"
PARENTS = frozenset({SAMPLE, "Quotation", "Sales Order", FLOW, "Work Order"})
HINTS = (
    "jf_sample_order", "jf_sample_order_item", "jf_sample_source_digest",
    "jf_sample_source_snapshot", "jf_sample_request_id", "jf_sample_request_digest",
    "jf_source_quotation", "jf_source_quotation_item",
)
IDENTITY = {"name", "parent", "parenttype", "parentfield"}
BATCH = 200
_AUTHORIZING = ContextVar("jifeng_sample_source_authorization", default=frozenset())
_CHECKING_DOCUMENTS = ContextVar("jifeng_sample_checking_documents", default=())
_SALES_HISTORY = ContextVar("jifeng_sample_sales_history", default=())
_PERMISSION_CALLS = ContextVar("jifeng_sample_permission_call", default=())
_DENIED = "无法完整读取样品及其来源，请沿用来源单据的角色、公司和字段权限"
_SHARE_DENIED = "样品及其关联单据不能独立分享，请沿用来源单据的角色、公司和字段权限"


def _deny(message=_DENIED):
    frappe.throw(message, frappe.PermissionError)


def _new(doc):
    return bool(doc.is_new())


def _key(doc, user):
    return (doc.doctype, doc.get("name"), user, id(doc) if _new(doc) else 0)


def _has_hint(doc):
    return any(doc.get(field) for field in HINTS)


def _reference(doctype, name, overrides=None):
    if not name:
        return None
    if overrides and (doctype, name) in overrides:
        return overrides[doctype, name]
    if not frappe.db.exists(doctype, name):
        return None
    return frappe.get_doc(doctype, name)


def _rows(doctype, filters, fields):
    """Internal identity/scope enumeration, never a public projection."""
    start = 0
    while True:
        batch = frappe.get_all(doctype, filters=filters, fields=fields,
                               order_by="name asc", limit_start=start, limit_page_length=BATCH)
        yield from batch
        if len(batch) < BATCH:
            return
        start += len(batch)


def _source_children(doc):
    # Inspect malformed hidden rows too: a wrong parenttype must not erase scope.
    if _new(doc):
        return
    for df in doc.meta.get_table_fields():
        if df.options not in {"Quotation Item", "Sales Order Item", SAMPLE_ITEM}:
            continue
        columns = set(frappe.get_meta(df.options).get_valid_columns())
        fields = sorted(IDENTITY | (set(HINTS) & columns)
                        | ({"prevdoc_docname", "quotation_item"} & columns))
        yield from _rows(df.options, {"parent": doc.name}, fields)


def _sales_row_scope(row, trail, overrides):
    if _has_hint(row):
        return True
    quote = _reference("Quotation", row.get("prevdoc_docname"), overrides)
    if quote and _scope(quote, trail, overrides):
        return True
    identity = row.get("quotation_item")
    if not identity:
        return False
    columns = set(frappe.get_meta("Quotation Item").get_valid_columns())
    raw = frappe.db.get_value("Quotation Item", identity,
                              sorted(IDENTITY | (set(HINTS) & columns)), as_dict=True)
    if not raw:
        return False
    if _has_hint(raw):
        return True
    quote = _reference("Quotation", raw.get("parent"), overrides)
    return bool(quote and _scope(quote, trail, overrides))


def _flow_scope(doc, trail, overrides):
    order = _reference("Sales Order", doc.get("sales_order"), overrides)
    if not order:
        return False
    matches = [row for row in order.get("items") or []
               if row.get("name") == doc.get("sales_order_item")]
    if len(matches) == 1:
        return _sales_row_scope(matches[0], trail, overrides)
    # An invalid child pointer to a sample-bearing order cannot drop protection.
    return _scope(order, trail, overrides)


def _scope(doc, trail=frozenset(), overrides=None):
    if doc is None or doc.doctype not in PARENTS:
        return False
    if doc.doctype == SAMPLE:
        return True
    key = (doc.doctype, doc.get("name") or id(doc))
    if key in trail:
        _deny()
    trail = trail | {key}
    if _has_hint(doc) or any(_has_hint(row) for row in doc.get("items") or []):
        return True
    rows = list(_source_children(doc))
    if any(_has_hint(row) for row in rows):
        return True
    if doc.doctype == "Sales Order":
        if any(_sales_row_scope(row, trail, overrides)
               for row in [*(doc.get("items") or []), *rows]):
            return True
    elif doc.doctype == FLOW and _flow_scope(doc, trail, overrides):
        return True
    elif doc.doctype == "Work Order":
        flow = _reference(FLOW, doc.get("jf_flow_order"), overrides)
        if flow and _scope(flow, trail, overrides):
            return True
        order = _reference("Sales Order", doc.get("sales_order"), overrides)
        if order and _scope(order, trail, overrides):
            return True
    origin = _reference(doc.doctype, doc.get("amended_from"), overrides)
    return bool(origin and _scope(origin, trail, overrides))


def _child_parent(doc):
    parenttype, parent, field = (doc.get(key) for key in ("parenttype", "parent", "parentfield"))
    if not parenttype or not parent or not field:
        _deny()
    parent_doc = _reference(parenttype, parent)
    if parent_doc is None or parent_doc.meta.istable:
        _deny()
    df = parent_doc.meta.get_field(field)
    if (not df or df.fieldtype not in {"Table", "Table MultiSelect"}
            or df.options != doc.doctype):
        _deny()
    matches = [row for row in parent_doc.get(field) or [] if row.name == doc.name]
    if len(matches) != 1 or any(matches[0].get(key) != doc.get(key) for key in IDENTITY):
        _deny()
    return parent_doc


def _storage_identity(doc):
    """Deny parent queries whose permissive native child join could add alien rows."""
    if _new(doc):
        return
    expected = {}
    for df in doc.meta.get_table_fields():
        expected.setdefault(df.options, set()).add(df.fieldname)
    for child, fields in expected.items():
        raw = list(_rows(child, {"parent": doc.name}, sorted(IDENTITY)))
        loaded = {row.name for field in fields for row in doc.get(field) or []}
        names = [row.get("name") for row in raw]
        if (len(names) != len(set(names)) or set(names) != loaded
                or any(row.get("parenttype") != doc.doctype
                       or row.get("parentfield") not in fields for row in raw)):
            _deny()


def _share_exists(filters, lock):
    if lock:
        # A locking current read sees a share committed while waiting for its
        # parent lock, rather than an earlier REPEATABLE READ snapshot.
        return frappe.db.get_value("DocShare", filters, "name", for_update=True)
    return frappe.db.exists("DocShare", filters)


def _unshared(doc, *, lock=False):
    # Native before_validate has assigned a new document's final name. An
    # orphan pre-existing share with that name must not become a real grant.
    if doc.get("name") and _share_exists({
            "share_doctype": doc.doctype, "share_name": doc.name}, lock):
        _deny(_SHARE_DENIED)
    children = {}
    for df in doc.meta.get_table_fields():
        children.setdefault(df.options, set()).update(
            row.get("name") for row in doc.get(df.fieldname) or [] if row.get("name"))
    for child, names in children.items():
        if names and _share_exists({
                "share_doctype": child, "share_name": ["in", sorted(names)]}, lock):
            _deny(_SHARE_DENIED)


def _complete_fields(doc):
    """Required source/business fields; unrelated fields retain native masking."""
    from jifeng_erp import sample_orders as samples
    from jifeng_erp import sample_quotations as quotes
    from jifeng_erp.flow_quantity import check_quantity_fields

    if doc.doctype == SAMPLE:
        samples._fields(SAMPLE, samples.PARENT_FIELDS | samples.APPROVAL
                        | {"name", "modified", "docstatus"})
        check_quantity_fields(SAMPLE, {"items"})
        samples._fields(SAMPLE_ITEM, samples.ITEM_FIELDS | IDENTITY, parenttype=SAMPLE)
    elif doc.doctype == "Quotation":
        check_quantity_fields("Quotation", quotes.QUOTE_FIELDS)
        check_quantity_fields("Quotation Item", quotes.QUOTE_ITEM_FIELDS, parenttype="Quotation")
    elif doc.doctype == "Sales Order":
        check_quantity_fields("Sales Order", ("name", "company", "customer", "items"))
        check_quantity_fields("Sales Order Item", quotes.SO_ITEM_FIELDS, parenttype="Sales Order")
    elif doc.doctype == "Work Order":
        samples._fields("Work Order", samples.WO_REQUIRED | {"jf_flow_order", "sales_order"})
    else:
        check_quantity_fields(FLOW, ("name", "company", "item_code", "sales_order",
                                    "sales_order_item", "source_key") + quotes.FLOW_SOURCE_FIELDS)


def _native_read(doc, user):
    # Never honor a document's ignore_permissions flag as a public read grant.
    if not frappe.has_permission(doc.doctype, "read", doc=doc, user=user,
                                 throw=False):
        _deny()


def _origins(doc):
    seen = set()
    name = doc.get("amended_from")
    while name:
        if name in seen:
            _deny()
        seen.add(name)
        original = _reference(doc.doctype, name)
        if original is None:
            _deny()
        yield original
        name = original.get("amended_from")


def _gate(doc, *, sales_order=None, sales_row=None):
    from jifeng_erp import sample_orders as samples
    from jifeng_erp import sample_quotations as quotes

    if doc.doctype != SAMPLE:
        from jifeng_erp.flow_quantity import check_quantity_fields

        columns = set(doc.meta.get_valid_columns())
        check_quantity_fields(doc.doctype, (set(HINTS) | {"amended_from"}) & columns)
    if doc.doctype == SAMPLE:
        # Native insert checks create before naming child rows or generating
        # server identities. These rows contain only caller input at this point.
        if _new(doc):
            return
        sources = samples._authorize(doc, active=False)
        if int(doc.get("docstatus") or 0) > 0:
            samples._approved(doc)
        sources.check()
    elif doc.doctype == "Quotation":
        if sales_order is not None:
            sample = _reference(SAMPLE, doc.get("jf_sample_order"))
            if sample is None:
                # Keep the fixed service's refusal, never synthesize a receipt.
                return quotes._historical_sales_quote_row(sales_order, sales_row, doc)
            return _check(sample, frappe.session.user, quotation=doc,
                          sales_order=sales_order, sales_row=sales_row)
        # Native amendment retains the old request fields, even when no_copy
        # sources are empty. Insert checks create before server normalization;
        # _check has already authorized the exact historical amendment origins.
        if _new(doc) and doc.get("amended_from"):
            return
        if not quotes._quotation_scope(doc):
            _deny()
        quotes._receipt(doc)
        sample = _reference(SAMPLE, doc.get("jf_sample_order"))
        if sample is None:
            # Preserve the business service's exact missing-source refusal.
            quotes._check_sources(doc, active=False, lock=False)
        else:
            _check(sample, frappe.session.user, quotation=doc)
    elif doc.doctype == "Sales Order":
        if _new(doc) and doc.get("amended_from"):
            return  # original is fully authorized in _check; save inherits exact rows
        candidates = [row for row in doc.get("items") or []
                      if _sales_row_scope(row, frozenset(), None)]
        if not candidates:
            _deny()
        for row in candidates:
            if _new(doc):
                quote, origin = quotes._quote_row(doc, row, active=False, lock=False)
            else:
                quote, origin = _saved_sales_quote_history(doc, row, frappe.session.user)
            if not _new(doc) or _has_hint(row):
                if any(row.get(field) != origin.get(field) for field in quotes.SOURCE_FIELDS):
                    _deny()
        # The native mapper intentionally leaves no_copy sources blank until
        # before_validate. Authorize its exact origin before normal insertion.
        check_quantity_fields("Sales Order", ("name", "company", "customer", "items"))
        check_quantity_fields("Sales Order Item", quotes.SO_ITEM_FIELDS, parenttype="Sales Order")
    elif doc.doctype == FLOW:
        quotes.check_sample_flow_source(doc, active=False, lock=False)
        if not quotes._sample_flow_candidate(doc):
            _deny()
    elif doc.doctype == "Work Order":
        if doc.get("jf_sample_order"):
            if _new(doc):
                sample = _reference(SAMPLE, doc.jf_sample_order)
                if sample is None:
                    _deny()
                _check(sample, frappe.session.user)
                samples._approved(sample)
                rows = [row for row in sample.get("items") or []
                        if row.name == doc.get("jf_sample_order_item")]
                if (len(rows) != 1 or doc.company != sample.company
                        or doc.production_item != rows[0].item_code
                        or doc.stock_uom != rows[0].stock_uom
                        or samples._decimal(doc.qty) != samples._decimal(rows[0].qty)
                        or doc.get("jf_sample_source_digest") != sample.get("approved_digest")):
                    _deny()
            else:
                samples._receipt(doc)
        elif any(doc.get(field) for field in HINTS):
            _deny()
        else:
            flow = _reference(FLOW, doc.get("jf_flow_order"))
            if flow is None or not _scope(flow):
                if _new(doc) and doc.get("amended_from"):
                    return
                _deny()
            _check(flow, frappe.session.user)
            if doc.company != flow.company or doc.production_item != flow.item_code:
                _deny()
        # Include the actual chosen BOM and generated warehouses/operations,
        # even when the approved sample row had no explicit BOM.
        samples._fields("Work Order", samples.WO_REQUIRED | {"jf_flow_order", "sales_order"})
        sources = samples._Sources(doc.company)
        if not doc.get("bom_no"):
            _deny()
        sources.bom(doc.bom_no, doc.production_item, active=False)
        sources._links(doc, active=False)
        for row in doc.get("required_items") or []:
            item, _ = sources.item(row.item_code, full=True, active=False)
            sources._item_defaults(item, active=False)
            sources._links(row, active=False)
        for row in doc.get("operations") or []:
            sources._links(row, active=False)
        sources.check()


def _quote_history_context(sample, quotation, user):
    """Only the active exact quotation gate may delegate its known sample read."""
    if (user != frappe.session.user or user == "Guest"
            or sample.doctype != SAMPLE or _new(sample) or not sample.get("name")
            or quotation.doctype != "Quotation"
            or quotation.get("jf_sample_order") != sample.name
            or _key(quotation, user) not in _AUTHORIZING.get()):
        _deny()


def _sales_history_context(order, row, quotation, user):
    """A live saved SO gate owns this exact loaded row, not an earlier read."""
    from jifeng_erp.sample_orders import _freeze

    frames = _SALES_HISTORY.get()
    frame = frames[-1] if frames else None
    if (not frame or frame["order"] is not order or frame["row"] is not row
            or frame["user"] != user or user != frappe.session.user or user == "Guest"
            or order.doctype != "Sales Order" or _new(order) or not order.get("name")
            or _key(order, user) not in _AUTHORIZING.get()
            or not any(doc is order and current_user == user
                       for doc, current_user in _CHECKING_DOCUMENTS.get())
            or _freeze(order) != frame["order_freeze"] or _freeze(row) != frame["row_freeze"]
            or quotation.doctype != "Quotation" or _new(quotation)
            or quotation.get("name") != row.get("prevdoc_docname")):
        _deny()
    df = order.meta.get_field("items")
    matches = [item for item in order.get("items") or [] if item.get("name") == row.get("name")]
    if (not df or df.fieldtype != "Table" or df.options != "Sales Order Item"
            or not row.get("name") or row.doctype != df.options
            or (row.parent, row.parenttype, row.parentfield) != (order.name, order.doctype, "items")
            or len(matches) != 1 or matches[0] is not row or not row.get("quotation_item")):
        _deny()


def _saved_sales_quote_history(order, row, user):
    """Only an active complete saved SO gate can open the fixed history frame."""
    from jifeng_erp.sample_orders import _freeze

    if (user != frappe.session.user or user == "Guest" or order.doctype != "Sales Order"
            or _new(order) or not order.get("name")
            or _key(order, user) not in _AUTHORIZING.get()
            or not any(doc is order and current_user == user
                       for doc, current_user in _CHECKING_DOCUMENTS.get())):
        _deny()
    current = _reference("Sales Order", order.name)
    if current is None or _freeze(current) != _freeze(order):
        _deny()
    quote = _reference("Quotation", row.get("prevdoc_docname"))
    if quote is None:
        _deny()
    frame = {"order": order, "row": row, "user": user,
             "order_freeze": _freeze(order), "row_freeze": _freeze(row)}
    token = _SALES_HISTORY.set((*_SALES_HISTORY.get(), frame))
    try:
        _sales_history_context(order, row, quote, user)
        return _check(quote, user, sales_order=order, sales_row=row)
    finally:
        _SALES_HISTORY.reset(token)


def _quote_history(sample, quotation, user, *, sales_order=None, sales_row=None):
    # This fixed historical service performs both _authorize / _approved /
    # sources.check passes and both parent projections before comparing freezes.
    # It is not an arbitrary business callback or a completed permission cache.
    from jifeng_erp import sample_quotations as quotes

    _quote_history_context(sample, quotation, user)
    if sales_order is None:
        result = quotes._check_sources(quotation, active=False, lock=False)
    else:
        _sales_history_context(sales_order, sales_row, quotation, user)
        result = quotes._historical_sales_quote_row(sales_order, sales_row, quotation)
        _sales_history_context(sales_order, sales_row, quotation, user)
    _quote_history_context(sample, quotation, user)
    return result


def _check(doc, user, *, quotation=None, sales_order=None, sales_row=None):
    sales_history = sales_order is not None or sales_row is not None
    if sales_history:
        if sales_order is None or sales_row is None:
            _deny()
        _sales_history_context(sales_order, sales_row, quotation or doc, user)
    if quotation is not None:
        _quote_history_context(doc, quotation, user)
    key = _key(doc, user)
    if key in _AUTHORIZING.get():
        if sales_history:
            _deny()  # an incomplete recursive gate cannot return a completed pair
        if quotation is not None:
            # The enclosing sample gate still owns the serial final checks;
            # do not omit quotation integrity just because it is recursive.
            _quote_history(doc, quotation, user)
        return
    if user != frappe.session.user or user == "Guest":
        _deny()
    _unshared(doc)
    _storage_identity(doc)
    token = _AUTHORIZING.set(_AUTHORIZING.get() | {key})
    try:
        _native_read(doc, user)
        _complete_fields(doc)
        # Validate and classify the entire chain before entering its first gate.
        # The direct parent's complete check repeats this rule for its parent;
        # checking every ancestor here too would expand full gates exponentially.
        # A plain intermediate version cannot cut off a protected older source.
        originals = list(_origins(doc))
        protected_origins = [_scope(original) for original in originals]
        if any(protected_origins):
            _check(originals[0], user)
        # This activity frame exists only inside a fully prechecked fixed body.
        # The enclosing SO body stays live through its nested Quote/Sample
        # final checks; their own body frames close before those final checks.
        documents_token = _CHECKING_DOCUMENTS.set((*_CHECKING_DOCUMENTS.get(), (doc, user)))
        try:
            if quotation is None:
                if sales_history:
                    result = _gate(doc, sales_order=sales_order, sales_row=sales_row)
                else:
                    result = _gate(doc)
            else:
                result = _quote_history(doc, quotation, user,
                                        sales_order=sales_order, sales_row=sales_row)
        finally:
            _CHECKING_DOCUMENTS.reset(documents_token)
        # Serial observation, not a claim of concurrent permission isolation.
        if not _new(doc):
            from jifeng_erp.sample_orders import _freeze

            current = frappe.get_doc(doc.doctype, doc.name)
            if _freeze(current) != _freeze(doc):
                _deny()
            _unshared(current)
            if quotation is not None or sales_history:
                _storage_identity(current)
                _complete_fields(current)
        _native_read(doc, user)
        if sales_history:
            _sales_history_context(sales_order, sales_row, quotation or doc, user)
        return result
    finally:
        _AUTHORIZING.reset(token)


def _permission_result(doc, ptype, user, result, subject=None):
    """Proof only for this exact Mixin super() call, never a request-level cache."""
    key = (id(doc), ptype, user)
    for frame in reversed(_PERMISSION_CALLS.get()):
        if frame["key"] == key:
            frame["result"] = bool(result)
            frame["subject"] = subject
            break
    return bool(result)


def sample_source_has_permission(doc, ptype, user=None, debug=False):
    """Additional necessary condition; True never grants a native permission."""
    user = user or frappe.session.user
    if _key(doc, user) in _AUTHORIZING.get():
        # This is a recursive native check inside an incomplete outer gate,
        # not a receipt of successfully completed source authorization.
        return True
    try:
        protected_subject = None
        if doc.doctype not in PARENTS:
            parent = _child_parent(doc)
            if _scope(parent):
                _check(parent, user)
                protected_subject = parent
            return _permission_result(doc, ptype, user, True, protected_subject)
        # Lazy and submitted payload projections cannot hide stored trace rows.
        stored = None if _new(doc) else _reference(doc.doctype, doc.name)
        subject = stored or doc
        # Even an ordinary parent must not expose alien rows through native
        # joins. This checks integrity, without requiring any sample source.
        _storage_identity(subject)
        if _scope(subject):
            _check(subject, user)
            protected_subject = subject
        elif _scope(doc):
            _check(doc, user)
            protected_subject = doc
        return _permission_result(doc, ptype, user, True, protected_subject)
    except (frappe.PermissionError, frappe.DoesNotExistError, frappe.ValidationError,
            ValueError, TypeError, KeyError):
        return _permission_result(doc, ptype, user, False)


def _sql_names(table, field, names):
    if not names:
        return "1=0"
    return f"`tab{table}`.`{field}` in (" + ",".join(frappe.db.escape(name) for name in names) + ")"


def sample_source_query_conditions(user=None, doctype=None):
    """Parent conditions also govern native child/count queries and link joins."""
    user = user or frappe.session.user
    if doctype not in PARENTS:
        return sample_child_query_conditions(user=user, doctype=doctype)
    if user != frappe.session.user or user == "Guest":
        return "1=0"
    active = sorted({key[1] for key in _AUTHORIZING.get()
                     if key[0] == doctype and key[2] == user and key[3] == 0 and key[1]})
    if active:
        return _sql_names(doctype, "name", active)
    names = []
    for row in _rows(doctype, {}, ["name"]):
        doc = frappe.get_doc(doctype, row.name)
        protected = _scope(doc)
        if protected:
            # With role-based access, an invariant violation must abort the
            # query rather than return a condition later OR'ed with shares.
            _unshared(doc)
        else:
            # The surrounding query still applies its ordinary owner/UP/share
            # conditions. Do not put native-unreadable ordinary names into the
            # additional IN clause (which may appear in debug SQL/trace).
            readable = frappe.has_permission(doctype, "read", doc=doc, user=user, throw=False)
            selectable = readable or frappe.has_permission(
                doctype, "select", doc=doc, user=user, throw=False)
            if not selectable:
                continue
        if sample_source_has_permission(doc, "read", user=user):
            names.append(row.name)
    return _sql_names(doctype, "name", names)


def _parent_contexts(child):
    candidates = set(PARENTS)
    for source, filters, column in (
            ("DocField", {"options": child}, "parent"),
            ("Custom Field", {"options": child}, "dt"),
            ("Property Setter", {"property": "options", "value": child}, "doc_type")):
        candidates.update(row.get(column) for row in _rows(source, filters, [column])
                          if row.get(column))
    for parenttype in sorted(candidates):
        meta = frappe.get_meta(parenttype)
        if meta.istable:
            continue
        fields = [df.fieldname for df in meta.get_table_fields() if df.options == child]
        if fields:
            yield parenttype, fields, meta


def sample_child_query_conditions(user=None, doctype=None):
    """For explicit child hook callers; native Engine normally uses parent PQC."""
    user = user or frappe.session.user
    if user != frappe.session.user or user == "Guest" or not doctype:
        return "1=0"
    pieces = []
    for parenttype, fields, meta in _parent_contexts(doctype):
        names = []
        candidates = [{"name": parenttype}] if meta.issingle else _rows(parenttype, {}, ["name"])
        for row in candidates:
            name = row.get("name")
            doc = frappe.get_doc(parenttype, name)
            if (frappe.has_permission(parenttype, "read", doc=doc, user=user,
                                      throw=False)
                    and (parenttype not in PARENTS
                         or sample_source_has_permission(doc, "read", user=user))):
                names.append(name)
        if names:
            pieces.append("(" + _sql_names(doctype, "parent", names)
                          + f" and `tab{doctype}`.`parenttype`=" + frappe.db.escape(parenttype)
                          + f" and `tab{doctype}`.`parentfield` in ("
                          + ",".join(frappe.db.escape(field) for field in fields) + "))")
    return "(" + " or ".join(pieces) + ")" if pieces else "1=0"


def _locked_parent(doc):
    if _new(doc) or not doc.get("name") or not frappe.db.exists(doc.doctype, doc.name):
        return None
    # Both independent sharing and source binding acquire this exact row first.
    frappe.db.get_value(doc.doctype, doc.name, "name", for_update=True)
    return frappe.get_doc(doc.doctype, doc.name, for_update=True)


def _descendants(doc, overrides, seen=None):
    """Also prevent a source change from making an already-shared child derived."""
    seen = set() if seen is None else seen
    key = (doc.doctype, doc.name)
    if key in seen:
        return
    seen.add(key)
    targets = []
    if doc.doctype == "Quotation":
        rows = _rows("Sales Order Item", {"prevdoc_docname": doc.name}, ["parent"])
        targets.extend(("Sales Order", row.parent) for row in rows)
        identities = [row.name for row in doc.get("items") or [] if row.get("name")]
        if identities:
            rows = _rows("Sales Order Item", {"quotation_item": ["in", identities]}, ["parent"])
            targets.extend(("Sales Order", row.parent) for row in rows)
    elif doc.doctype == "Sales Order":
        targets.extend((FLOW, row.name) for row in _rows(FLOW, {"sales_order": doc.name}, ["name"]))
        targets.extend(("Work Order", row.name) for row in _rows("Work Order", {"sales_order": doc.name}, ["name"]))
    elif doc.doctype == FLOW:
        targets.extend(("Work Order", row.name) for row in _rows("Work Order", {"jf_flow_order": doc.name}, ["name"]))
    for doctype, name in sorted(set(targets)):
        target = _reference(doctype, name, overrides)
        if target is not None and _scope(target, overrides=overrides):
            current = _locked_parent(target) or target
            if _scope(current, overrides=overrides):
                _unshared(current, lock=True)
                _descendants(current, overrides, seen)


def before_validate_source_binding(doc, method=None):
    """Serialize trace binding with DocShare, without approving the business write."""
    if doc.doctype not in PARENTS:
        return
    previous = _locked_parent(doc)
    if _scope(doc) or previous and _scope(previous):
        _unshared(doc, lock=True)
        if previous:
            _unshared(previous, lock=True)
        if doc.get("name"):
            _descendants(doc, {(doc.doctype, doc.name): doc})


def validate_sample_source_share(doc, method=None, share=None):
    """Native parent validate_share; ordinary documents retain native sharing."""
    parent = _child_parent(doc) if doc.doctype not in PARENTS else doc
    current = _locked_parent(parent) or parent
    if _scope(current):
        _deny(_SHARE_DENIED)


def before_validate_docshare(document, method=None):
    doctype, name = document.get("share_doctype"), document.get("share_name")
    if not doctype or not name:
        return
    if doctype not in PARENTS:
        meta = frappe.get_meta(doctype)
        if not meta.istable:
            return
        parents = [parent for parent in PARENTS if any(
            df.options == doctype for df in frappe.get_meta(parent).get_table_fields())]
        if not parents:
            return
    doc = _reference(doctype, name)
    if doc is None:
        # These are known parent/child types. Ignore-validate sharing must not
        # leave a grant for a future document that acquires this name.
        _deny(_SHARE_DENIED)
    validate_sample_source_share(doc, document)


def assert_sample_share_invariant():
    """Read-only deployment gate; generic failure, no share/source IDs returned.

    Required before relying on app-only PQC. Native share-only queries skip PQC
    entirely, so detecting bad shares only inside a query hook is insufficient.
    This never deletes/revokes a share or claims that direct SQL is mediated.
    """
    for row in _rows("DocShare", {}, ["share_doctype", "share_name"]):
        if row.share_doctype in PARENTS:
            doc = _reference(row.share_doctype, row.share_name)
            if doc is None or _scope(doc):
                _deny(_SHARE_DENIED)
        else:
            meta = frappe.get_meta(row.share_doctype)
            if meta.istable and any(df.options == row.share_doctype
                                   for parent in PARENTS
                                   for df in frappe.get_meta(parent).get_table_fields()):
                doc = _reference(row.share_doctype, row.share_name)
                if doc is None or _scope(_child_parent(doc)):
                    _deny(_SHARE_DENIED)


class SampleSourcePermissionMixin:
    """Optional extend_doctype_class final guard for native direct child reads."""

    def has_permission(self, permtype="read", *, debug=False, user=None):
        user = user or frappe.session.user
        frame = {"key": (id(self), permtype, user), "result": None, "subject": None}
        token = _PERMISSION_CALLS.set((*_PERMISSION_CALLS.get(), frame))
        try:
            allowed = super().has_permission(permtype, debug=debug, user=user)
            if not allowed:
                return False
            # Native DocShare can revive a False controller result. Preserve
            # that denial rather than rerun an expensive complete source gate.
            if frame["result"] is False:
                return False
            if frame["result"] is None:
                # Administrator, ignore_permissions and native child routing
                # may skip this controller hook. They still need the final gate.
                if not sample_source_has_permission(self, permtype, user, debug):
                    return False
            subject = frame["subject"]
            if subject is not None:
                # Final share-only supplement to the already completed gate.
                try:
                    _unshared(subject)
                except frappe.PermissionError:
                    return False
            return True
        finally:
            _PERMISSION_CALLS.reset(token)
