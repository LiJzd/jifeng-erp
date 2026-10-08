"""Narrow default read access needed by the native POS client."""

from jifeng_erp.receipt_export_permissions import permission_projection, same_permission_rules


ROLES = ("Sales User", "Accounts User", "System Manager")
READ_ROLES = ("Sales Manager", "Accounts Manager")


def _native_default(fields):
    rows = []
    for role in ROLES:
        row = {field: 0 for field in fields}
        row.update(role=role, permlevel=0)
        for flag in ("read", "write", "email", "print", "share"):
            if flag not in row:
                raise ValueError("POS Settings permission schema is incomplete")
            row[flag] = 1
        rows.append(row)
    return rows


def pos_settings_read_policy(native, custom, *, fields):
    """Grant only the two manager roles on an exact, uncustomized native site."""
    fields = tuple(fields)
    if not {"role", "permlevel", "if_owner", "read", "write", "create", "delete",
            "submit", "cancel", "report", "export", "share", "print", "email"} <= set(fields):
        return "permission_schema_requires_review", []

    native_rows = permission_projection(native, fields=fields)
    if not same_permission_rules(native_rows, _native_default(fields), fields=fields):
        return "native_rule_not_applicable", native_rows

    desired = list(native_rows)
    for role in READ_ROLES:
        row = {field: 0 for field in fields}
        row.update(role=role, permlevel=0, read=1)
        desired.append(row)

    if custom:
        if same_permission_rules(custom, desired, fields=fields):
            return "already_installed", desired
        return "site_policy_preserved", desired
    return "install_default", desired
