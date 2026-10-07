"""Private native queries for exact parents in one cost source closure.

Each Engine instance supplies fresh matching identities to the existing sample
permission hook. Global no-independent-share checks and every native permission
hook remain active. No permission result or population survives a query.
"""

from copy import deepcopy

import frappe


_PARENTS = frozenset(("Jifeng Flow Order", "Work Order"))


def record_list(doctype, *, filters, fields, parenttype=None, **options):
    """Keep ordinary sources native; narrow only an exact Flow/WO identity.

    Child filters stay parent-only so the caller's complete raw/native comparison
    still rejects corrupt parenttype/parentfield rows instead of hiding them.
    """
    native_options = {"parent_doctype": parenttype} if parenttype else {}
    target = parenttype or doctype
    if target not in _PARENTS:
        return frappe.get_list(doctype, filters=filters, fields=fields, order_by="name asc",
                               limit_page_length=0, **native_options, **options)
    identity = "parent" if parenttype else "name"
    if (not isinstance(filters, dict) or set(filters) != {identity}
            or not isinstance(filters[identity], str) or not filters[identity]
            or filters[identity] != filters[identity].strip() or options):
        raise frappe.PermissionError
    actor = frappe.session.user
    if not actor or actor == "Guest":
        raise frappe.PermissionError
    fixed_filters, fixed_fields = deepcopy(filters), tuple(fields)
    if parenttype:
        tables = [df for df in frappe.get_meta(parenttype).get_table_fields()
                  if df.options == doctype and df.fieldtype == "Table"]
        if len(tables) != 1:
            raise frappe.PermissionError

    from frappe.database.query import Engine, RawCriterion
    from frappe.core.doctype.server_script.server_script_utils import get_server_script_map
    from jifeng_erp import sample_source_permissions as sources

    class FixedCostQuery(Engine):
        def get_permission_query_conditions(self, permission_doctype=None):
            permission_target = permission_doctype or self.permission_doctype
            if (self.doctype != doctype or self.permission_doctype != target
                    or self.user != actor or actor != frappe.session.user
                    or not self.apply_permissions or self.ignore_user_permissions
                    or permission_target != target):
                raise frappe.PermissionError
            conditions = []
            hooks = frappe.get_hooks("permission_query_conditions", {})
            for method in hooks.get(target, []) + hooks.get("*", []):
                if actor != frappe.session.user:
                    raise frappe.PermissionError
                function = frappe.get_attr(method)
                if function is sources.sample_source_query_conditions:
                    # Query again at this exact native hook position: preceding
                    # callbacks cannot make a prior raw population into an allow.
                    column = "parent" if parenttype else "name"
                    rows = frappe.get_all(doctype, filters=deepcopy(fixed_filters),
                                          fields=[column], order_by="name asc", limit_page_length=0)
                    if any(not isinstance(row.get(column), str) or not row[column] for row in rows):
                        raise frappe.PermissionError
                    names = tuple(sorted({row[column] for row in rows}))
                    condition = sources._query_conditions(user=self.user, doctype=target,
                                                           candidate_names=names)
                else:
                    # Nested calls inherit neither this instance nor its scope.
                    condition = frappe.call(function, self.user, doctype=target)
                if condition:
                    conditions.append(RawCriterion(f"({condition})"))
            current_tables = self.get_queried_tables()
            child_tables = [name for name in current_tables if name != f"tab{self.doctype}"]
            if script_name := get_server_script_map().get("permission_query", {}).get(target):
                script = frappe.get_doc("Server Script", script_name)
                if condition := script.get_permission_query_conditions(self.user, active_child_tables=child_tables):
                    conditions.append(RawCriterion(f"({condition})"))
            return conditions

    query = FixedCostQuery().get_query(table=doctype, fields=list(fixed_fields),
        filters=deepcopy(fixed_filters), order_by="name asc", limit=0,
        ignore_permissions=False, ignore_user_permissions=False, user=actor,
        parent_doctype=parenttype, db_query_compat=True)
    if actor != frappe.session.user:
        raise frappe.PermissionError
    # Native execution applies its usual field masks and parent metadata.
    return query.run(as_dict=True)
