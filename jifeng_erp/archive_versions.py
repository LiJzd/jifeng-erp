"""Compare native modified timestamps without changing their precision."""

from datetime import datetime
import re


_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\Z")


def same_version(current, supplied):
    """Frappe saves a space string; database reads yield a datetime/ISO string."""
    if not isinstance(supplied, str) or not _TIMESTAMP.fullmatch(supplied):
        return False
    if not isinstance(current, datetime):
        if not isinstance(current, str) or not _TIMESTAMP.fullmatch(current):
            return False
        try:
            current = datetime.fromisoformat(current)
        except ValueError:
            return False
    try:
        return current.tzinfo is None and current == datetime.fromisoformat(supplied)
    except ValueError:
        return False
