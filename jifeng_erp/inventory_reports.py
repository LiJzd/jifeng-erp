"""Read-only inventory reports, including native reconciliation balance resets."""

from collections import defaultdict

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import cint, getdate

from jifeng_erp.inventory_scope import (
    column, decimal_number, readable_documents, report_scope, selected_warehouses, stock_items,
)


def _completed_valuation(company, end):
    from erpnext.stock.utils import check_pending_reposting

    if check_pending_reposting(end, company, throw_error=False):
        frappe.throw("所选日期内仍有库存估价重算任务，请在重算完成后查询")


def _complete_ledger(query):
    """Never present an apparently complete balance after dropping hidden SLEs.

    SLE authorizes inventory figures independently of the source voucher's role.
    No source voucher fields or links are exposed by the balance report. Construct
    full SLE documents from the bulk rows to also honor controller/user permissions.
    """
    records = frappe.get_all("Stock Ledger Entry", filters=query, fields=["*"], limit_page_length=0)
    visible = set(frappe.get_list("Stock Ledger Entry", filters=query, pluck="name", limit_page_length=0))
    if {row.name for row in records} != visible:
        frappe.throw("无法完整读取所选范围的库存流水，不能计算部分库存余额", frappe.PermissionError)
    meta = frappe.get_meta("Stock Ledger Entry")
    # The report derives every balance and valuation from these native numeric
    # fields. Document-level read access alone does not authorize masked fields.
    numeric_fields = ("actual_qty", "qty_after_transaction", "valuation_rate", "stock_value",
                      "stock_value_difference")
    readable_fields = set(get_permitted_fields(
        "Stock Ledger Entry", permission_type="read", ignore_virtual=True))
    mask_levels = set(meta.get_permlevel_access(permission_type="mask", user=frappe.session.user))
    for fieldname in numeric_fields:
        field = meta.get_field(fieldname)
        if (fieldname not in readable_fields or field is None or
                (field.mask and (field.permlevel or 0) in mask_levels)):
            frappe.throw("无权读取库存流水数量或估价字段，不能计算库存余额", frappe.PermissionError)
    tables = meta.get_table_fields()
    for record in records:
        doc = (frappe.get_doc("Stock Ledger Entry", record.name) if tables else
               frappe.get_doc({**record, "doctype": "Stock Ledger Entry"}))
        if not doc.has_permission("read"):
            frappe.throw("无法完整读取所选范围的库存流水，不能计算部分库存余额", frappe.PermissionError)
        for field in numeric_fields:
            decimal_number(record.get(field))
    return records


def _source_metadata(entries, company):
    """Validate bounded ledger provenance without returning private voucher data."""
    groups = defaultdict(set)
    for entry in entries:
        if not entry.voucher_type or not entry.voucher_no:
            frappe.throw("库存流水缺少来源凭证，请先核对账簿")
        groups[entry.voucher_type].add(entry.voucher_no)
    opening = defaultdict(list)
    for doctype, names in groups.items():
        meta = frappe.get_meta(doctype)
        fields = ["name", "docstatus"]
        if meta.has_field("company"):
            fields.append("company")
        if doctype == "Stock Entry":
            fields.append("is_opening")
        elif doctype == "Stock Reconciliation":
            fields.append("purpose")
        sources = frappe.get_all(doctype, filters={"name": ["in", sorted(names)]}, fields=fields, limit_page_length=0)
        if {source.name for source in sources} != names:
            frappe.throw("库存流水的来源凭证不完整，请先核对账簿")
        for source in sources:
            if cint(source.docstatus) != 1 or ("company" in fields and source.company != company):
                frappe.throw("库存流水与来源凭证的公司或提交状态不一致，请先核对账簿")
            if ((doctype == "Stock Entry" and source.is_opening == "Yes") or
                    (doctype == "Stock Reconciliation" and source.purpose == "Opening Stock")):
                opening[doctype].append(source.name)
    return dict(opening)


def _native_balance(filters, entries, opening):
    from erpnext.stock.report.stock_balance.stock_balance import StockBalanceReport

    class AuthorizedBalance(StockBalanceReport):
        def prepare_opening_stock(self):
            # Replaying all authorized history must not ALSO load a closing snapshot.
            pass

        def prepare_sle_query(self):
            super().prepare_sle_query()
            sle = frappe.qb.DocType("Stock Ledger Entry")
            self.sle_query = self.sle_query.where(sle.name.isin([entry.name for entry in entries]))

        def get_opening_vouchers(self):
            return opening

        def prepare_stock_reco_voucher_wise_count(self):
            # The native helper has a second unrestricted SLE query. Derive its
            # serial-row special case solely from the authorized history instead.
            self.stock_reco_voucher_wise_count = frappe._dict()
            grouped = defaultdict(list)
            for entry in entries:
                if entry.voucher_type == "Stock Reconciliation" and cint(entry.has_serial_no):
                    grouped[entry.voucher_detail_no].append(entry)
            for name, rows in grouped.items():
                if len(rows) != 1:
                    continue
                source = frappe.db.get_value("Stock Reconciliation Item", name,
                                             ["current_qty", "qty", "parent", "parenttype"], as_dict=True)
                if not source or source.parent != rows[0].voucher_no or source.parenttype != "Stock Reconciliation":
                    frappe.throw("序列号盘点库存流水与来源明细不一致")
                if source.qty and source.current_qty:
                    self.stock_reco_voucher_wise_count[name] = source.current_qty

        def get_sre_reserved_qty_details(self):
            # Today's reservation is not a historical stock figure.
            return {}

        def prepare_item_warehouse_map(self, entry, group_by_key):
            super().prepare_item_warehouse_map(entry, group_by_key)
            if self.filters.include_zero_stock_items:
                if not hasattr(self, "history_map"):
                    self.history_map = {}
                self.history_map[group_by_key] = frappe._dict(self.item_warehouse_map[group_by_key].copy())

        def prepare_item_warehouse_map_for_current_period(self):
            super().prepare_item_warehouse_map_for_current_period()
            # Native pruning also removes historical zero balances with no period
            # turnover. "Include zero" retains only combinations that had a SLE.
            for key, row in getattr(self, "history_map", {}).items():
                self.item_warehouse_map.setdefault(key, row)
            # Native filter_items_with_no_transactions applies flt() to currency
            # as well as numeric fields. Preserve the native Company's currency
            # instead of returning the corrupted 0.0 alias for nonzero balances.
            for row in self.item_warehouse_map.values():
                row["currency"] = self.company_currency

    return AuthorizedBalance(filters).run()[1]


def historical_stock_balance(filters=None):
    filters, start, end = report_scope(filters, ("Stock Ledger Entry",))
    columns = _balance_columns()
    warehouses, items = selected_warehouses(filters), stock_items(filters)
    if not warehouses or not items:
        return columns, []
    _completed_valuation(filters.company, end)
    entries = _complete_ledger({
        "company": filters.company, "warehouse": ["in", warehouses], "item_code": ["in", items],
        "posting_date": ["<=", end], "docstatus": ["<", 2], "is_cancelled": 0,
    })
    if not entries:
        return columns, []
    units = {}
    for entry in entries:
        if entry.item_code not in units:
            units[entry.item_code] = frappe.get_cached_value("Item", entry.item_code, "stock_uom")
        if not units[entry.item_code] or entry.stock_uom != units[entry.item_code]:
            frappe.throw("历史库存流水与物料库存单位不一致，请先核对账簿")
    opening = _source_metadata(entries, filters.company)
    native_filters = frappe._dict(
        company=filters.company, from_date=start, to_date=end, warehouse=warehouses, item_code=items,
        ignore_closing_balance=1, include_zero_stock_items=cint(filters.include_zero),
    )
    result = _native_balance(native_filters, entries, opening)
    # Explicit output fields prevent internal native/source fields from escaping.
    fields = [col["fieldname"] for col in columns]
    data = [{field: row.get(field) for field in fields} for row in result]
    currency = frappe.get_cached_value("Company", filters.company, "default_currency")
    for row in data:
        row["currency"] = currency
    return columns, sorted(data, key=lambda row: (row["item_code"], row["warehouse"]))


def _balance_columns():
    return [
        column("item_code", "物料", "Link", "Item", 170), column("item_name", "名称", width=150),
        column("warehouse", "仓库", "Link", "Warehouse", 180), column("stock_uom", "库存单位", width=90),
        column("opening_qty", "期初数量", "Float"), column("in_qty", "期间增加数量", "Float"),
        column("out_qty", "期间减少数量", "Float"), column("bal_qty", "期末数量", "Float"),
        column("currency", "公司本币", width=90),
        column("opening_val", "期初库存价值", "Currency", "currency"),
        column("in_val", "期间价值增加", "Currency", "currency"),
        column("out_val", "期间价值减少", "Currency", "currency"),
        column("bal_val", "期末库存价值", "Currency", "currency"),
        column("val_rate", "期末估价单价", "Currency", "currency"),
        column("company", "公司", "Link", "Company", 180),
    ]


def _same_number(left, right, precision=6):
    return abs(decimal_number(left) - decimal_number(right)) <= decimal_number(10) ** -precision


def reconciliation_movement(document, row, entries, currency, dimensions=()):
    """A reconciliation's qty is the counted balance, never an incoming quantity."""
    before_qty, after_qty = decimal_number(row.current_qty), decimal_number(row.qty)
    before_value, counted_value = decimal_number(row.current_amount), decimal_number(row.amount)
    quantity_difference = after_qty - before_qty
    value_difference = decimal_number(row.amount_difference)
    adjustment = bool(entries) and all(cint(entry.is_adjustment_entry) for entry in entries)
    if any(cint(entry.is_adjustment_entry) for entry in entries) and not adjustment:
        frappe.throw("同一盘点明细混有数量盘点与价值冲销流水，请核对账簿")
    ledger_value = 0
    for entry in entries:
        if (entry.voucher_type != "Stock Reconciliation" or entry.voucher_no != document.name or
                entry.voucher_detail_no != row.name or entry.company != document.company or
                entry.item_code != row.item_code or entry.warehouse != row.warehouse or
                entry.stock_uom != row.stock_uom or getdate(entry.posting_date) != getdate(document.posting_date) or
                cint(entry.is_cancelled) or cint(entry.docstatus) >= 2):
            frappe.throw("盘点明细与库存流水的来源、公司、物料、仓库或单位不一致")
        ledger_value += decimal_number(entry.stock_value_difference)
    if not _same_number(quantity_difference, row.quantity_difference) or not _same_number(value_difference, ledger_value):
        frappe.throw("盘点差异与已过账库存流水不一致，请在估价重算完成后核对")
    if adjustment:
        if quantity_difference or any(decimal_number(entry.actual_qty) for entry in entries):
            frappe.throw("价值冲销流水不能改变盘点数量")
        after_value = before_value + ledger_value
    else:
        after_value = counted_value
        if not _same_number(after_value - before_value, value_difference):
            frappe.throw("盘点前后价值与差异金额不一致，请核对来源凭证")
        if not entries and (quantity_difference or value_difference):
            frappe.throw("盘点差异缺少已过账库存流水")
        if entries:
            if any(row.get(field) for field in dimensions):
                frappe.throw("所选盘点使用库存维度，请先通过原生盘点单和库存账簿核对维度数量")
            if row.get("current_serial_and_batch_bundle") or row.get("serial_and_batch_bundle"):
                outgoing = -sum(min(decimal_number(entry.actual_qty), 0) for entry in entries)
                incoming = sum(max(decimal_number(entry.actual_qty), 0) for entry in entries)
                if not _same_number(outgoing, before_qty) or not _same_number(incoming, after_qty):
                    frappe.throw("批次或序列号盘点的盘前移出、盘后移入数量与明细不一致")
            elif any(entry.get("batch_no") or entry.get("serial_no") for entry in entries):
                frappe.throw("所选盘点使用旧批次或序列号流水，请先通过原生盘点单核对数量")
            elif len(entries) != 1 or not _same_number(entries[0].qty_after_transaction, after_qty):
                frappe.throw("盘点后的库存流水余额与盘点数量不一致")
    return {
        "reconciliation": document.name, "reconciliation_item": row.name, "posting_date": document.posting_date,
        "purpose": "期初库存" if document.purpose == "Opening Stock" else "库存盘点",
        "item_code": row.item_code, "item_name": row.item_name, "warehouse": row.warehouse,
        "stock_uom": row.stock_uom, "before_qty": before_qty, "after_qty": after_qty,
        "quantity_difference": quantity_difference, "currency": currency, "before_value": before_value,
        "after_value": after_value, "value_difference": value_difference, "ledger_value_difference": ledger_value,
        "adjustment_only": int(adjustment), "company": document.company,
    }


def stock_reconciliation_ledger(filters=None):
    filters, start, end = report_scope(filters, ("Stock Reconciliation", "Stock Ledger Entry"))
    columns = _reconciliation_columns()
    warehouses, items = set(selected_warehouses(filters)), set(stock_items(filters))
    if not warehouses or not items:
        return columns, []
    _completed_valuation(filters.company, end)
    documents = readable_documents("Stock Reconciliation", {
        "company": filters.company, "docstatus": 1, "posting_date": ["between", [start, end]],
    })
    selected = [(doc, row) for doc in documents.values() for row in doc.items
                if row.item_code in items and row.warehouse in warehouses]
    if not selected:
        return columns, []
    entries = _complete_ledger({
        "company": filters.company, "voucher_type": "Stock Reconciliation",
        "voucher_no": ["in", sorted({doc.name for doc, row in selected})],
        "voucher_detail_no": ["in", [row.name for doc, row in selected]],
        "is_cancelled": 0, "docstatus": ["<", 2],
    })
    by_row = defaultdict(list)
    for entry in entries:
        by_row[(entry.voucher_no, entry.voucher_detail_no)].append(entry)
    currency = frappe.get_cached_value("Company", filters.company, "default_currency")
    from erpnext.stock.doctype.inventory_dimension.inventory_dimension import get_inventory_dimensions
    dimensions = [dimension.fieldname for dimension in get_inventory_dimensions()]
    data = []
    for doc, row in selected:
        item = frappe.get_cached_doc("Item", row.item_code)
        if row.stock_uom != item.stock_uom:
            frappe.throw("盘点明细与物料库存单位不一致")
        movement = reconciliation_movement(doc, row, by_row[(doc.name, row.name)], currency, dimensions)
        if cint(filters.only_difference) and not movement["quantity_difference"] and not movement["value_difference"]:
            continue
        data.append(movement)
    return columns, sorted(data, key=lambda row: (getdate(row["posting_date"]), row["reconciliation"], row["reconciliation_item"]))


def _reconciliation_columns():
    return [
        column("reconciliation", "盘点单", "Link", "Stock Reconciliation", 190),
        column("reconciliation_item", "盘点明细来源", width=170), column("posting_date", "过账日期", "Date"),
        column("purpose", "用途", width=95), column("item_code", "物料", "Link", "Item", 170),
        column("item_name", "名称", width=150), column("warehouse", "仓库", "Link", "Warehouse", 180),
        column("stock_uom", "库存单位", width=90), column("before_qty", "盘前数量", "Float"),
        column("after_qty", "盘后数量", "Float"), column("quantity_difference", "数量差异", "Float"),
        column("currency", "公司本币", width=90), column("before_value", "盘前库存价值", "Currency", "currency"),
        column("after_value", "盘后库存价值", "Currency", "currency"),
        column("value_difference", "价值差异", "Currency", "currency"),
        column("ledger_value_difference", "账簿价值变动", "Currency", "currency"),
        column("adjustment_only", "仅价值冲销", "Check"), column("company", "公司", "Link", "Company", 180),
    ]
