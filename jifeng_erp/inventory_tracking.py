"""Inventory tracking rows from exact archives and native historical balances.

An archive's reference price/unit/weight never values or converts stock. Native
StockBalance supplies quantity and valuation, including reconciliation resets.
All contributing ledger rows must remain readable; no partial balance is sent.
Guards cover this request, not an atomic database or cross-request snapshot.
"""

from decimal import Decimal, InvalidOperation

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
def query_tracking(kind, company, search="", warehouse=None, as_of=None, page=1, page_size=catalog.LIMIT):
    """Page checked history pairs; balance access never exposes source vouchers."""
    page = catalog._page_integer(page, "页码", catalog.MAX_SAFE_INTEGER)
    page_size = catalog._page_integer(page_size, "每页条数", catalog.LIMIT)
    result, _entries, currency, cutoff = _checked_tracking(kind, company, search, warehouse, as_of)
    return {"kind": kind, "company": company, "search": search, "warehouse": warehouse or "",
            "as_of": cutoff, "currency": currency, "basis": dict(BASIS),
            **_page(result, page, page_size), "readonly": True, "can_write": False}


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
