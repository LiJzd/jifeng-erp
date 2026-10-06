"""Private fixed Graph query adapter for the inspected Frappe 16 QB engine.

Each instance owns one immutable query population. It does not cache allows,
install a global hook, change frappe.get_list, or expose an RPC entry point.
"""

from copy import deepcopy

import frappe


_PARENTS = frozenset(("Sales Order", "Jifeng Flow Order", "Work Order"))


def record_list(doctype, *, filters, fields, parenttype=None, limit, scoped=False):
    options = {"parent_doctype": parenttype} if parenttype else {}
    if not scoped or (parenttype or doctype) not in _PARENTS:
        return frappe.get_list(doctype, filters=filters, fields=fields, order_by="name asc",
                               limit_page_length=limit, **options)
    if not isinstance(filters, dict) or not isinstance(limit, int) or not 0 < limit <= 1001:
        raise frappe.PermissionError
    fixed_filters, fixed_fields = deepcopy(filters), tuple(fields)
    actor = frappe.session.user
    if not actor or actor == "Guest":
        raise frappe.PermissionError
    if parenttype:
        fieldname = fixed_filters.get("parentfield")
        df = frappe.get_meta(parenttype).get_field(fieldname)
        if (fixed_filters.get("parenttype") != parenttype or not fieldname or not df
                or df.fieldtype != "Table" or df.options != doctype):
            raise frappe.PermissionError

    from frappe.database.query import Engine, RawCriterion
    from frappe.core.doctype.server_script.server_script_utils import get_server_script_map
    from jifeng_erp import sample_source_permissions as sources

    class FixedQuery(Engine):
        def get_permission_query_conditions(self, permission_doctype=None):
            # Native Frappe 16 database/query.py:1626-1654, with only the exact
            # app hook call changed. Other hooks and Server Scripts stay native.
            target = permission_doctype or self.permission_doctype
            if (self.doctype != doctype or self.permission_doctype != (parenttype or doctype)
                    or self.user != actor or actor != frappe.session.user
                    or not self.apply_permissions or self.ignore_user_permissions
                    or target != (parenttype or doctype)):
                raise frappe.PermissionError
            conditions = []
            hooks = frappe.get_hooks("permission_query_conditions", {})
            for method in hooks.get(target, []) + hooks.get("*", []):
                if actor != frappe.session.user:
                    raise frappe.PermissionError
                function = frappe.get_attr(method)
                if function is sources.sample_source_query_conditions:
                    # Recompute at the exact hook position, after preceding
                    # native callbacks. No allow result or prior population is reused.
                    column = "parent" if parenttype else "name"
                    rows = frappe.get_all(doctype, filters=deepcopy(fixed_filters),
                                          fields=[column], order_by="name asc",
                                          limit_page_length=limit)
                    if len(rows) >= limit or any(not isinstance(row.get(column), str)
                                                or not row.get(column) for row in rows):
                        raise frappe.PermissionError
                    names = tuple(sorted({row[column] for row in rows}))
                    condition = sources._query_conditions(user=self.user, doctype=target,
                                                           candidate_names=names)
                else:
                    # Nested native calls have no instance or ambient population
                    # to inherit. Even a same-doctype nested PQC is unmodified.
                    condition = frappe.call(function, self.user, doctype=target)
                if condition:
                    conditions.append(RawCriterion(f"({condition})"))
            active_child_tables = []
            current_tables = self.get_queried_tables()
            if len(current_tables) > 1:
                main_table_name = f"tab{self.doctype}"
                active_child_tables = [name for name in current_tables if name != main_table_name]
            if script_name := get_server_script_map().get("permission_query", {}).get(target):
                script = frappe.get_doc("Server Script", script_name)
                if condition := script.get_permission_query_conditions(
                        self.user, active_child_tables=active_child_tables):
                    conditions.append(RawCriterion(f"({condition})"))
            return conditions

    query = FixedQuery().get_query(table=doctype, fields=list(fixed_fields),
                                  filters=deepcopy(fixed_filters), order_by="name asc", limit=limit,
                                  ignore_permissions=False, ignore_user_permissions=False,
                                  user=actor, parent_doctype=parenttype, db_query_compat=True)
    if actor != frappe.session.user:
        raise frappe.PermissionError
    # Native query.run retains the Engine's _doctype/_fields_list/parent metadata
    # and the existing execution-time mask_fields processing.
    return query.run(as_dict=True)
