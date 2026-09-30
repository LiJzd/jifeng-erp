"""Recognize default Separation rules without replacing site permission policy."""

from collections import Counter


PERMISSION_FIELDS = (
    "role", "permlevel", "if_owner", "read", "write", "create", "delete", "submit", "cancel",
    "amend", "report", "export", "import", "share", "print", "email", "select", "mask",
)


def default_separation_permissions(native, custom, *, fields=PERMISSION_FIELDS):
    """Accept a complete native clone and our paired HR Manager submit/cancel.

    Partial pairs, changed ownership/levels, different roles or any other changed
    right are site policy. No ESS selection exception applies to Separation.
    """
    def shape(row):
        return tuple(str(row.get(field) or "") if field == "role" else int(row.get(field) or 0)
                     for field in fields)

    baseline = Counter(shape(row) for row in native)
    normalized = []
    for original in custom:
        row = dict(original)
        if (row.get("role") == "HR Manager" and int(row.get("permlevel") or 0) == 0
                and not int(row.get("if_owner") or 0)
                and int(row.get("submit") or 0) == int(row.get("cancel") or 0) == 1):
            candidate = {**row, "submit": 0, "cancel": 0}
            if shape(candidate) in baseline:
                row = candidate
        normalized.append(shape(row))
    return Counter(normalized) == baseline
