"""Recognize native permission clones without replacing site-specific decisions."""

from collections import Counter


PERMISSION_FIELDS = (
    "role", "permlevel", "if_owner", "read", "write", "create", "delete", "submit", "cancel",
    "amend", "report", "export", "import", "share", "print", "email", "select", "mask",
)


def default_wage_permissions(native, custom, *, ess_generated=False, fields=PERMISSION_FIELDS):
    """Accept exact native rules plus the verified HRMS ESS selection rule.

    The only permitted difference to a native HR Manager rule is this app's
    paired cancel/delete extension. Any extra role or other changed right wins.
    """
    def shape(row):
        return tuple(str(row.get(field) or "") if field == "role" else int(row.get(field) or 0)
                     for field in fields)

    baseline = Counter(shape(row) for row in native)
    expected_ess = {field: 0 for field in fields}
    expected_ess.update(role="Employee Self Service", select=1, export=1)
    normalized = []
    ess_count = 0
    for original in custom:
        row = dict(original)
        if row.get("role") == "Employee Self Service":
            if not ess_generated or shape(row) != shape(expected_ess):
                return False
            ess_count += 1
            continue
        if row.get("role") == "HR Manager" and int(row.get("permlevel") or 0) == 0:
            if int(row.get("cancel") or 0) == 1 and int(row.get("delete") or 0) == 1:
                candidate = {**row, "cancel": 0, "delete": 0}
                if shape(candidate) in baseline:
                    row = candidate
        normalized.append(shape(row))
    return ess_count <= 1 and Counter(normalized) == baseline
