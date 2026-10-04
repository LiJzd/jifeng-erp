"""Inventory tracking rows from exact archives and native historical balances.

An archive's reference price/unit/weight never values or converts stock. Native
StockBalance supplies quantity and valuation, including reconciliation resets.
All contributing ledger rows must remain readable; no partial balance is sent.
Guards cover this request, not an atomic database or cross-request snapshot.
"""

from decimal import Decimal, InvalidOperation
from functools import cmp_to_key
import json

import frappe
from frappe.utils import getdate, today

from jifeng_erp import inventory_catalog as catalog
from jifeng_erp import inventory_reports as reports


PROFILE_FIELDS = {"legacy_number", "display_name", "major_category", "minor_category", "material",
                  "specification", "location", "notes", "weight", "display_uom", "shape", "color",
                  "old_number", "owner_name", "region", "supplier"}
WAREHOUSE_FIELDS = {"name", "modified", "company", "is_group"}
ITEM_REQUIRED = {"name", "modified", "item_code", "stock_uom", "is_stock_item"}
LEDGER_FIELDS = {
    "name", "modified", "creation", "company", "item_code", "warehouse", "stock_uom", "posting_date",
    "posting_datetime", "docstatus", "is_cancelled", "actual_qty", "qty_after_transaction",
    "valuation_rate", "stock_value", "stock_value_difference", "voucher_type", "voucher_no",
    "voucher_detail_no", "batch_no", "serial_no", "serial_and_batch_bundle", "has_serial_no",
    "is_adjustment_entry",
}
LEDGER_NUMBERS = {"actual_qty", "qty_after_transaction", "valuation_rate", "stock_value", "stock_value_difference"}
BASIS = {
    "stock_qty": "原生 StockBalance 截止日库存数量；仅真实库存流水组合，包含历史零余额",
    "valuation_rate": "原生 StockBalance 截止日估价单价，不是档案参考价或已核实的旧系统单价算法",
    "stock_value": "原生 StockBalance 截止日库存估值，不按档案参考价计算",
    "stock_uom": "关联 Item 的真实库存单位，不使用档案换算值",
    "profile_weight": "档案已保存重量原值；重量单位及与库存数量的换算关系未核实",
    "profile_display_uom": "档案单位原文，不作为库存数量单位",
    "total_weight": "总重量算法未核实，留空",
}

# These are the rendered tracking columns, never raw archive/Item fields. A
# masked or omitted optional field remains absent in the authorized projection.
COLUMN_FIELDS = {
    "物料": ("warehouse", "legacy_number", "stock_qty", "valuation_rate", "stock_value", "weight",
             "total_weight", "major_category", "minor_category", "material", "stock_uom", "display_name",
             "specification", "location", "notes", "supplier"),
    "材料": ("warehouse", "legacy_number", "stock_qty", "valuation_rate", "stock_value", "weight",
             "total_weight", "minor_category", "specification", "shape", "color", "display_name", "location",
             "old_number", "owner_name", "region", "supplier"),
}
NUMERIC_COLUMNS = {"stock_qty", "valuation_rate", "stock_value", "weight", "total_weight"}
ROW_COLUMNS = {"warehouse", "stock_qty", "valuation_rate", "stock_value", "stock_uom", "total_weight"}
TEXT_OPERATORS = {"eq", "ne", "starts", "not_starts", "ends", "not_ends", "contains", "not_contains"}
MAX_COLUMN_VALUE = 2000
MAX_SELECTED_VALUES = 200


def _bad_columns():
    frappe.throw("列筛选或排序条件无效；每列最多选择 200 个值，单个值最多 2000 字")


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _column_json(value, maximum):
    try:
        if isinstance(value, str):
            if len(value) > maximum:
                _bad_columns()
            value = json.loads(value, object_pairs_hook=_unique_json_object)
        if len(json.dumps(value, ensure_ascii=False)) > maximum:
            _bad_columns()
    except (TypeError, ValueError, RecursionError):
        _bad_columns()
    return value


def _column_value(value):
    return (isinstance(value, str) and len(value) <= MAX_COLUMN_VALUE
            and not any(ord(char) < 32 and char not in "\n\r\t" or ord(char) == 127 for char in value))


def _column_query(kind, column_filters, sort):
    extended = column_filters is not None or sort is not None
    filters = _column_json(column_filters, 65536) if column_filters is not None else []
    order = _column_json(sort, 2048) if sort is not None else None
    fields = COLUMN_FIELDS.get(kind, ()) if isinstance(kind, str) else ()
    if not isinstance(filters, list) or len(filters) > len(fields):
        _bad_columns()
    seen = set()
    for condition in filters:
        if (not isinstance(condition, dict) or set(condition) != {"field", "operator", "value"}
                or not isinstance(condition["field"], str) or condition["field"] not in fields
                or condition["field"] in seen or not isinstance(condition["operator"], str)):
            _bad_columns()
        seen.add(condition["field"])
        operator, value = condition["operator"], condition["value"]
        if operator in TEXT_OPERATORS:
            if not _column_value(value):
                _bad_columns()
        elif operator in {"in", "not_in"}:
            if (not isinstance(value, list) or len(value) > MAX_SELECTED_VALUES
                    or any(item is not None and not _column_value(item) for item in value)
                    or len({json.dumps(item, ensure_ascii=False) for item in value}) != len(value)):
                _bad_columns()
        else:
            _bad_columns()
    if order is not None and (not isinstance(order, dict) or set(order) != {"field", "direction"}
                              or not isinstance(order["field"], str) or order["field"] not in fields
                              or order["direction"] not in ("asc", "desc")):
        _bad_columns()
    return filters, order, extended


def _projected_value(row, field):
    return row.get(field) if field in ROW_COLUMNS else row["profile"].get(field)


def _column_match(row, condition):
    value = _projected_value(row, condition["field"])
    operator, expected = condition["operator"], condition["value"]
    if operator in {"in", "not_in"}:
        # Null is an empty projection; a literal em dash is a different value.
        return (value in expected) == (operator == "in")
    text = "—" if value is None else str(value)
    comparisons = {"eq": lambda: text == expected, "ne": lambda: text != expected,
                   "starts": lambda: text.startswith(expected), "not_starts": lambda: not text.startswith(expected),
                   "ends": lambda: text.endswith(expected), "not_ends": lambda: not text.endswith(expected),
                   "contains": lambda: expected in text, "not_contains": lambda: expected not in text}
    return comparisons[operator]()


def _column_rows(rows, filters, order):
    rows = [row for row in rows if all(_column_match(row, condition) for condition in filters)]
    if order is None:
        return rows

    def compare(left, right):
        a, b = (_projected_value(row, order["field"]) for row in (left, right))
        if a is None or b is None:
            # Missing values stay last for either direction.
            comparison = (a is None) - (b is None)
        else:
            if order["field"] in NUMERIC_COLUMNS:
                a, b = Decimal(a), Decimal(b)
            comparison = (a > b) - (a < b)
            if order["direction"] == "desc":
                comparison = -comparison
        if comparison:
            return comparison
        a, b = (tuple(row[key] for key in ("warehouse", "profile_name", "item_code")) for row in (left, right))
        return (a > b) - (a < b)

    return sorted(rows, key=cmp_to_key(compare))


def _deny():
    frappe.throw("无法完整读取库存跟踪的档案、商品、仓库或库存流水，请重新核对权限", frappe.PermissionError)


def _invalid():
    frappe.throw("库存跟踪来源、单位或估值不完整，请先核对原生库存账簿")


def _number(value):
    if value is None or isinstance(value, bool):
        _invalid()
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        _invalid()
    if not number.is_finite():
        _invalid()
    return str(number)


def _fields(doctype, required):
    if not set(required).issubset(catalog._columns(doctype)):
        _deny()


def _currency(company):
    document = frappe.get_doc("Company", company)
    document.check_permission("select")
    # Native stock roles may select Company without reading its full profile.
    # A masked currency must not be recovered through a derived balance alias.
    if frappe.session.user != "Administrator":
        meta = frappe.get_meta("Company")
        field = meta.get_field("default_currency")
        levels = set(meta.get_permlevel_access("mask", parenttype=None))
        if field and field.get("mask") and (field.permlevel or 0) not in levels:
            _deny()
    currency = document.get("default_currency")
    if not isinstance(currency, str) or not currency.strip() or currency != currency.strip():
        _invalid()
    return currency


def _date(value):
    if value is None:
        value = today()
    if not isinstance(value, str) or len(value) != 10:
        frappe.throw("请选择有效的 YYYY-MM-DD 库存截止日期")
    try:
        cutoff = getdate(value)
    except (TypeError, ValueError):
        frappe.throw("请选择有效的 YYYY-MM-DD 库存截止日期")
    if str(cutoff) != value:
        frappe.throw("请选择有效的 YYYY-MM-DD 库存截止日期")
    return cutoff


def _warehouses(company, selected):
    if not frappe.has_permission("Warehouse", "read"):
        _deny()
    _fields("Warehouse", WAREHOUSE_FIELDS)
    query = {"company": company, "is_group": 0}
    if selected:
        query["name"] = selected
    raw = frappe.get_all("Warehouse", filters=query, fields=sorted(WAREHOUSE_FIELDS), limit_page_length=0)
    names = {row.get("name") for row in raw}
    visible = set(frappe.get_list("Warehouse", filters=query, pluck="name", limit_page_length=0))
    if names != visible or len(names) != len(raw):
        _deny()
    if selected and names != {selected}:
        _deny()
    documents = []
    for row in raw:
        doc = frappe.get_doc("Warehouse", row["name"])
        if (not catalog._current(doc, row, WAREHOUSE_FIELDS - {"modified"})
                or row["company"] != company or row["is_group"]):
            _deny()
        documents.append(row)
    _fields("Warehouse", WAREHOUSE_FIELDS)
    return documents


def _archives(kind, company, search):
    # A readable archive whose referenced Item is missing/hidden cannot silently
    # disappear from tracking. Initially invisible archives stay outside scope.
    rows = catalog._scan_candidate_rows(kind, company, search, "全部")
    packed = catalog._query_sources(kind, company, search, "全部")
    if _state(rows) != _state([guard["row"] for _view, guard in packed]):
        _deny()
    _fields("Item", ITEM_REQUIRED)
    items = {}
    for view, guard in packed:
        name = view["item_code"]
        if name in items:
            _invalid()
        document = frappe.get_doc("Item", name)
        if (not catalog._current(document, guard["item_row"], catalog.ITEM_REQUIRED - {"modified"})
                or not document.get("is_stock_item") or not document.get("stock_uom")
                or guard["item_row"].get("stock_uom") != document.get("stock_uom")):
            _deny()
        items[name] = view
    _fields("Item", ITEM_REQUIRED)
    return items


def _state(rows):
    # Compare complete detached source projections; never expose raw ledger data.
    return tuple(sorted((tuple(sorted((key, type(value).__name__, str(catalog._value(value)))
                                     for key, value in row.items())) for row in rows), key=repr))


def _ledger(company, cutoff, items, warehouses, dimensions):
    _fields("Stock Ledger Entry", LEDGER_FIELDS | set(dimensions))
    query = {"company": company, "warehouse": ["in", sorted(warehouses)],
             "item_code": ["in", sorted(items)], "posting_date": ["<=", cutoff],
             "docstatus": ["<", 2], "is_cancelled": 0}
    entries = reports._complete_ledger(query)
    seen = set()
    for row in entries:
        if (not LEDGER_FIELDS.issubset(row) or row["name"] in seen or row["company"] != company
                or row["item_code"] not in items or row["warehouse"] not in warehouses
                or row["stock_uom"] != items[row["item_code"]]["item"]["stock_uom"]
                or getdate(row["posting_date"]) > cutoff or row["is_cancelled"] or row["docstatus"] >= 2):
            _invalid()
        seen.add(row["name"])
        for field in LEDGER_NUMBERS:
            _number(row[field])
    _fields("Stock Ledger Entry", LEDGER_FIELDS | set(dimensions))
    return entries


def _project(native, items, warehouses, entries, company, currency):
    combinations = {(row["item_code"], row["warehouse"]) for row in entries}
    seen, result = set(), []
    for row in native:
        key = row.get("item_code"), row.get("warehouse")
        if (key in seen or key not in combinations or row.get("company") != company
                or row.get("stock_uom") != items[key[0]]["item"]["stock_uom"]
                or (row.get("currency") not in (None, currency))):
            _invalid()
        seen.add(key)
        view = items[key[0]]
        profile = {field: value for field, value in view["profile"].items() if field in PROFILE_FIELDS}
        if profile.get("weight") is not None:
            profile["weight"] = _number(profile["weight"])
        result.append({
            "profile_name": view["name"], "profile_version": view["profile_version"],
            "item_code": key[0], "item_version": view["item_version"], "warehouse": key[1],
            "warehouse_version": catalog._value(warehouses[key[1]]["modified"]),
            "stock_uom": row["stock_uom"], "stock_qty": _number(row.get("bal_qty")),
            "valuation_rate": _number(row.get("val_rate")), "stock_value": _number(row.get("bal_val")),
            "profile": profile, "image": view["image"], "total_weight": None,
        })
    # include_zero in the existing native wrapper retains every historic pair.
    if seen != combinations:
        _invalid()
    return sorted(result, key=lambda row: (row["warehouse"], row["profile_name"], row["item_code"]))


def _checked_tracking(kind, company, search, warehouse, as_of):
    """Read the complete authorized scope before either paging or drilling in."""
    catalog._scope(kind, company)
    if warehouse is not None and (not isinstance(warehouse, str) or not warehouse or warehouse != warehouse.strip()):
        frappe.throw("请选择准确仓库，或不指定仓库")
    cutoff = _date(as_of)
    if not frappe.has_permission("Stock Ledger Entry", "read"):
        _deny()
    from erpnext.stock.doctype.inventory_dimension.inventory_dimension import get_inventory_dimensions

    dimensions = tuple(sorted(dimension.fieldname for dimension in get_inventory_dimensions()))
    access = {doctype: catalog._columns(doctype) for doctype in
              (catalog.PROFILE, "Item", "Warehouse", "Stock Ledger Entry", "File")}
    currency = _currency(company)
    items = _archives(kind, company, search)
    warehouses = {row["name"]: row for row in _warehouses(company, warehouse)}
    reports._completed_valuation(company, cutoff)
    entries = _ledger(company, cutoff, items, warehouses, dimensions) if items and warehouses else []
    opening = reports._source_metadata(entries, company) if entries else {}
    filters = frappe._dict(company=company, from_date=cutoff, to_date=cutoff,
                           warehouse=sorted(warehouses), item_code=sorted(items),
                           ignore_closing_balance=1, include_zero_stock_items=1)
    native = reports._native_balance(filters, entries, opening) if entries else []
    result = _project(native, items, warehouses, entries, company, currency)

    # Read and authorize all sources again after native calculation/image loads.
    fresh_items = _archives(kind, company, search)
    fresh_warehouses = {row["name"]: row for row in _warehouses(company, warehouse)}
    fresh_entries = _ledger(company, cutoff, fresh_items, fresh_warehouses, dimensions) if items and warehouses else []
    fresh_opening = reports._source_metadata(fresh_entries, company) if fresh_entries else {}
    fresh_native = reports._native_balance(filters, fresh_entries, fresh_opening) if fresh_entries else []
    fresh_result = _project(fresh_native, fresh_items, fresh_warehouses, fresh_entries, company, currency)
    # Native serial reconciliation may read its exact child quantity again.
    # That second native pass is also untrusted until its source permissions
    # and data have been rechecked; it cannot be the last operation's authority.
    final_items = _archives(kind, company, search)
    final_warehouses = {row["name"]: row for row in _warehouses(company, warehouse)}
    final_entries = _ledger(company, cutoff, final_items, final_warehouses, dimensions) if items and warehouses else []
    final_opening = reports._source_metadata(final_entries, company) if final_entries else {}
    catalog._scope(kind, company)
    reports._completed_valuation(company, cutoff)
    if (items != fresh_items or _state(warehouses.values()) != _state(fresh_warehouses.values())
            or _state(entries) != _state(fresh_entries) or opening != fresh_opening
            or result != fresh_result
            or items != final_items or _state(warehouses.values()) != _state(final_warehouses.values())
            or _state(entries) != _state(final_entries) or opening != final_opening
            or currency != _currency(company)
            or dimensions != tuple(sorted(dimension.fieldname for dimension in get_inventory_dimensions()))
            or access != {doctype: catalog._columns(doctype) for doctype in access}):
        _deny()
    return result, entries, currency, str(cutoff)


def _page(rows, page, page_size):
    total = len(rows)
    total_pages = (total + page_size - 1) // page_size
    if page > max(1, total_pages):
        frappe.throw("页码超过查询结果范围，请重新查询")
    start = (page - 1) * page_size
    return {"page": page, "page_size": page_size, "total": total, "total_pages": total_pages,
            "rows": rows[start:start + page_size]}


@frappe.whitelist()
def query_tracking(kind, company, search="", warehouse=None, as_of=None, page=1, page_size=catalog.LIMIT,
                   column_filters=None, sort=None):
    """Page checked history pairs; balance access never exposes source vouchers."""
    page = catalog._page_integer(page, "页码", catalog.MAX_SAFE_INTEGER)
    page_size = catalog._page_integer(page_size, "每页条数", catalog.LIMIT)
    conditions, order, extended = _column_query(kind, column_filters, sort)
    result, _entries, currency, cutoff = _checked_tracking(kind, company, search, warehouse, as_of)
    # Filtering cannot hide an unreadable or changed source on another page.
    result = _column_rows(result, conditions, order)
    return {"kind": kind, "company": company, "search": search, "warehouse": warehouse or "",
            "as_of": cutoff, "currency": currency, "basis": dict(BASIS),
            **({"filter_protocol": 1, "column_filters": conditions, "sort": order} if extended else {}),
            **_page(result, page, page_size), "readonly": True, "can_write": False}


@frappe.whitelist()
def query_tracking_values(kind, company, field, search="", warehouse=None, as_of=None,
                          column_filters=None, sort=None, page=1, page_size=catalog.LIMIT):
    """Distinct values from the entire checked scope, with bounded option pages.

    Other column conditions apply; this column's own condition is excluded so
    previously unchecked values can be selected again. No raw source fallback.
    """
    conditions, order, _extended = _column_query(kind, column_filters, sort)
    if not isinstance(kind, str) or not isinstance(field, str) or field not in COLUMN_FIELDS.get(kind, ()):
        _bad_columns()
    page = catalog._page_integer(page, "选项页码", catalog.MAX_SAFE_INTEGER)
    page_size = catalog._page_integer(page_size, "每页选项数", catalog.LIMIT)
    rows, _entries, currency, cutoff = _checked_tracking(kind, company, search, warehouse, as_of)
    matched = _column_rows(rows, [condition for condition in conditions if condition["field"] != field], None)
    values = {_projected_value(row, field) for row in matched}
    if any(value is not None and not _column_value(value) for value in values):
        frappe.throw("该列包含超过 2000 字或不可选择的值，请使用文本筛选")
    values = sorted(values, key=lambda value: (value is None, value or ""))
    result = _page(values, page, page_size)
    result["values"] = result.pop("rows")
    return {"kind": kind, "company": company, "search": search, "warehouse": warehouse or "",
            "as_of": cutoff, "currency": currency, "filter_protocol": 1, "column_filters": conditions,
            "sort": order, "field": field, "full_scope": True, "source_total": len(rows),
            "matched_total": len(matched), **result, "readonly": True, "can_write": False}


def _selection(rows, profile_name, item_code, warehouse, profile_version, item_version, warehouse_version):
    expected = {"profile_name": profile_name, "item_code": item_code, "warehouse": warehouse,
                "profile_version": profile_version, "item_version": item_version,
                "warehouse_version": warehouse_version}
    if any(not isinstance(value, str) or not value or value != value.strip() or len(value) > 140
           or any(ord(character) < 32 or ord(character) == 127 for character in value)
           for value in expected.values()):
        frappe.throw("请选择准确的库存跟踪行，并保留其来源版本")
    found = [row for row in rows if all(row[field] == value for field, value in expected.items())]
    if len(found) != 1:
        frappe.throw("库存跟踪来源或版本已变化，请重新查询", frappe.PermissionError)
    return found[0]


@frappe.whitelist()
def read_tracking_archive(kind, company, profile_name, item_code, warehouse, as_of,
                          profile_version, item_version, warehouse_version, search=""):
    """Read-only drawer; do not navigate away or grant an archive edit action."""
    archive = catalog.read_archive(profile_name, kind, company)
    # The archive drawer's stock table covers this company's complete leaf scope.
    rows, _entries, currency, cutoff = _checked_tracking(kind, company, search, None, as_of)
    selected = _selection(rows, profile_name, item_code, warehouse,
                          profile_version, item_version, warehouse_version)
    if (archive["item_code"] != item_code or archive["profile_version"] != profile_version
            or archive["item_version"] != item_version
            or archive != catalog.read_archive(profile_name, kind, company)):
        _deny()
    balances = [row for row in rows if row["profile_name"] == profile_name and row["item_code"] == item_code]
    return {"kind": kind, "company": company, "search": search, "warehouse": warehouse,
            "as_of": cutoff, "currency": currency, "row": selected, "archive": archive,
            "balances": balances, "readonly": True, "can_write": False}


def _voucher_sources(entries, company):
    """Voucher identifiers need their own permissions, independent of SLE read."""
    groups = {}
    for row in entries:
        groups.setdefault(row["voucher_type"], set()).add(row["voucher_no"])
    state, loaded = {}, []
    for doctype, names in groups.items():
        if not frappe.has_permission(doctype, "read"):
            _deny()
        required = {"name", "modified", "docstatus"}
        if frappe.get_meta(doctype).has_field("company"):
            required.add("company")
        _fields(doctype, required)
        records = frappe.get_list(doctype, filters={"name": ["in", sorted(names)]},
                                  fields=sorted(required), limit_page_length=0)
        if len(records) != len(names) or {row.get("name") for row in records} != names:
            _deny()
        for row in records:
            document = frappe.get_doc(doctype, row["name"])
            loaded.append((doctype, required, row, document))
        state[doctype] = (tuple(sorted(required)), _state(records))
    # A later voucher load must not hide an earlier voucher after it was checked.
    for doctype, required, row, document in loaded:
        _fields(doctype, required)
        if (not frappe.has_permission(doctype, "read") or not required.issubset(row)
                or not catalog._current(document, row, required - {"modified"})
                or row["docstatus"] != 1 or "company" in required and row["company"] != company):
            _deny()
    return state


def _record_rows(entries, company):
    rows = []
    for entry in sorted(entries, key=lambda row: (str(row["posting_datetime"]), str(row["creation"]), row["name"])):
        if not entry.get("posting_datetime") or entry.get("is_adjustment_entry") not in (0, 1, False, True):
            _invalid()
        rows.append({"name": entry["name"], "modified": catalog._value(entry["modified"]),
                     "posting_datetime": catalog._value(entry["posting_datetime"]),
                     "voucher_type": entry["voucher_type"], "voucher_no": entry["voucher_no"],
                     **{field: _number(entry[field]) for field in LEDGER_NUMBERS},
                     "stock_uom": entry["stock_uom"], "is_adjustment_entry": int(entry["is_adjustment_entry"]),
                     "company": company})
    return rows


@frappe.whitelist()
def query_tracking_records(kind, company, profile_name, item_code, warehouse, as_of,
                           profile_version, item_version, warehouse_version, search="",
                           page=1, page_size=catalog.LIMIT):
    """Native movements for one exact pair, including absolute reconciliation.

    actual_qty and qty_after_transaction remain distinct native values. Neither
    is relabelled as an unverified old-system receipt/return/count category.
    Source and field authorization covers every record before pagination.
    """
    page = catalog._page_integer(page, "页码", catalog.MAX_SAFE_INTEGER)
    page_size = catalog._page_integer(page_size, "每页条数", catalog.LIMIT)
    rows, entries, currency, cutoff = _checked_tracking(kind, company, search, warehouse, as_of)
    selected = _selection(rows, profile_name, item_code, warehouse,
                          profile_version, item_version, warehouse_version)
    entries = [entry for entry in entries if entry["item_code"] == item_code and entry["warehouse"] == warehouse]
    source_state = _voucher_sources(entries, company)
    records = _record_rows(entries, company)
    # Voucher loads can change an earlier source's permissions. Recheck the full
    # inventory scope and then every voucher; this is not an atomic DB snapshot.
    fresh_rows, fresh_entries, fresh_currency, fresh_cutoff = _checked_tracking(kind, company, search, warehouse, as_of)
    fresh_selected = _selection(fresh_rows, profile_name, item_code, warehouse,
                                profile_version, item_version, warehouse_version)
    fresh_entries = [entry for entry in fresh_entries if entry["item_code"] == item_code and entry["warehouse"] == warehouse]
    if (selected != fresh_selected or currency != fresh_currency or cutoff != fresh_cutoff
            or _state(entries) != _state(fresh_entries) or source_state != _voucher_sources(fresh_entries, company)):
        _deny()
    return {"kind": kind, "company": company, "search": search, "warehouse": warehouse,
            "as_of": cutoff, "currency": currency, "row": selected,
            **_page(records, page, page_size), "readonly": True, "can_write": False}
