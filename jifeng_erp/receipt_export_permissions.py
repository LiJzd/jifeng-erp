"""Default receipt export policy for the existing stock supervisor role."""

from collections import Counter


PERMISSION_FIELDS = (
    "role", "permlevel", "if_owner", "read", "write", "create", "delete", "submit", "cancel",
    "amend", "report", "export", "import", "share", "print", "email", "select", "mask",
)


def permission_projection(rows, *, fields=PERMISSION_FIELDS):
    """Project every permission flag without retaining row names or timestamps."""
    projected = []
    for row in rows:
        result = {}
        for field in fields:
            value = row.get(field)
            if field == "role":
                if not isinstance(value, str) or not value:
                    raise ValueError("Permission role is required")
                result[field] = value
            else:
                if value in (None, ""):
                    value = 0
                if not (isinstance(value, (int, bool)) or isinstance(value, str) and value.isdigit()):
                    raise ValueError("Permission values must be integers")
                number = int(value)
                if number < 0 or field != "permlevel" and number not in (0, 1):
                    raise ValueError("Permission value is outside its native range")
                result[field] = number
        projected.append(result)
    return projected


def same_permission_rules(left, right, *, fields=PERMISSION_FIELDS):
    def shape(rows):
        return Counter(tuple(row[field] for field in fields)
                       for row in permission_projection(rows, fields=fields))

    return shape(left) == shape(right)


def stock_receipt_export_policy(native, custom, *, fields=PERMISSION_FIELDS):
    """Enable export on an uncustomized, unconditional Stock Manager read rule.

    Existing Custom DocPerm rows are site policy, including an exact native
    clone with export disabled. Installation never replaces those decisions.
    """
    if not {"role", "permlevel", "if_owner", "read", "export"} <= set(fields):
        raise ValueError("Native permission schema is incomplete")
    desired = permission_projection(native, fields=fields)
    targets = [row for row in desired if row["role"] == "Stock Manager" and row["permlevel"] == 0]
    if len(targets) != 1 or targets[0]["if_owner"] or not targets[0]["read"]:
        return "native_rule_not_applicable", desired
    targets[0]["export"] = 1
    if custom:
        status = "already_installed" if same_permission_rules(custom, desired, fields=fields) else "site_policy_preserved"
        return status, desired
    if same_permission_rules(native, desired, fields=fields):
        return "native_already_enabled", desired
    return "install_default", desired
