"""Create source-bound, editable returns for posted Material Issue vouchers.

Only Material Issue rows with complete, unambiguous native stock ledger and
original serial/batch valuation are supported. Return drafts use Material Receipt
posting and cancellation flow; this module never posts automatically.
"""

from collections import defaultdict
from decimal import Decimal
import json

import frappe
from frappe.model import get_permitted_fields

from jifeng_erp.material_issue_locking import _lock_flows, _persisted_flow_ids


SOURCE_FIELD = "jf_material_return_against"
SOURCE_ROW_FIELD = "jf_material_return_against_detail"
FLOW_FIELD = "jf_material_issue_flow"
FLOW_ROW_FIELD = "jf_flow_order"
REPOST = "Repost Item Valuation"
SLE = "Stock Ledger Entry"
BUNDLE = "Serial and Batch Bundle"
BUNDLE_ENTRY = "Serial and Batch Entry"
TRACKING_FIELDS = ("batch_no", "serial_no", "serial_and_batch_bundle", "use_serial_batch_fields")
BUNDLE_FIELDS = ("name", "docstatus", "company", "item_code", "warehouse", "voucher_type",
                 "voucher_no", "voucher_detail_no", "type_of_transaction", "is_cancelled", "total_qty",
                 "total_amount", "has_serial_no", "has_batch_no")
ENTRY_FIELDS = ("name", "docstatus", "parent", "parenttype", "parentfield", "item_code", "warehouse",
                "serial_no", "batch_no", "qty", "incoming_rate", "stock_value_difference", "is_cancelled")
MAX_RELATED = 1000
TOLERANCE = Decimal("0.000001")
_SOURCE_ERROR = "原领料来源、公司、仓库或库存账无法完整核对，不能生成领料退库。"

# Read-only costing readers supply these exact, permission/change-closed
# documents and child sets. These helpers never acquire a writer lock or save.
MATERIAL_RETURN_READ_PARENTS = {
    "Stock Entry": {"name", "docstatus", "company", "purpose", "stock_entry_type", "is_return",
                    SOURCE_FIELD, FLOW_FIELD, "total_additional_costs"},
    BUNDLE: set(BUNDLE_FIELDS),
    "Batch": {"name", "item"},
    "Serial No": {"name", "item_code", "batch_no"},
    "Item": {"name", "stock_uom", "is_stock_item", "has_serial_no", "has_batch_no", "use_serial_no_wise_valuation"},
}
MATERIAL_RETURN_READ_TABLES = {
    BUNDLE: (("entries", BUNDLE_ENTRY, set(ENTRY_FIELDS)),),
    "Stock Entry": (("items", "Stock Entry Detail", {"name", "item_code", "qty", "uom", "stock_uom",
        "conversion_factor", "transfer_qty", "s_warehouse", "t_warehouse", "expense_account", "cost_center", "project",
        SOURCE_ROW_FIELD, FLOW_ROW_FIELD, "set_basic_rate_manually", "basic_rate", "additional_cost",
        "landed_cost_voucher_amount", *TRACKING_FIELDS}),
        ("additional_costs", "Landed Cost Taxes and Charges", {"name", "amount"})),
}


class MaterialReturnUsage(dict):
    """Per original detail, including each posted lot's actual returned value."""

    def __init__(self):
        super().__init__()
        self.values_used = defaultdict(Decimal)

    def copy(self):
        result = MaterialReturnUsage()
        result.update(self)
        result.values_used.update(self.values_used)
        return result


def _throw(message=_SOURCE_ERROR, exc=frappe.ValidationError):
    frappe.throw(message, exc)


def _permitted_fields(doc, fields, permission="read", parenttype=None):
    if frappe.session.user == "Administrator":
        return
    if isinstance(doc, str):
        meta = frappe.get_meta(doc)
    elif getattr(doc, "doctype", None) == "DocType" and getattr(doc, "name", None):
        # Frappe Meta objects have doctype="DocType" and name=<actual doctype>.
        meta = doc
    else:
        meta = doc.meta
    # get_permitted_fieldnames is the Meta scalar projection and omits standard
    # fields such as docstatus/name. Use Frappe's authoritative permission model
    # for scalar fields, while retaining this Meta object for field masks.
    permitted = set(get_permitted_fields(
        meta.name, parenttype=parenttype, permission_type=permission, ignore_virtual=True))
    if not set(fields).issubset(permitted):
        frappe.throw("没有读取或填写领料退库所需字段的权限", frappe.PermissionError)
    mask_levels = set(meta.get_permlevel_access(permission_type="mask", parenttype=parenttype,
                                                user=frappe.session.user))
    if any((field := meta.get_field(name)) and field.get("mask")
           and (field.permlevel or 0) not in mask_levels for name in fields):
        frappe.throw("没有读取或填写领料退库所需字段的权限", frappe.PermissionError)


def _permitted_query_fields(doctype, fields):
    if frappe.session.user == "Administrator":
        return
    permitted = set(get_permitted_fields(doctype, permission_type="read", ignore_virtual=True))
    if not set(fields).issubset(permitted):
        frappe.throw("没有读取原领料库存价值字段的权限", frappe.PermissionError)
    meta = frappe.get_meta(doctype)
    mask_levels = set(meta.get_permlevel_access(permission_type="mask", user=frappe.session.user))
    if any((field := meta.get_field(name)) and field.get("mask")
           and (field.permlevel or 0) not in mask_levels for name in fields):
        frappe.throw("没有读取原领料库存价值字段的权限", frappe.PermissionError)


def _permitted_table(doc, fieldname, childtype, fields, permission="read"):
    if frappe.session.user == "Administrator":
        return
    field = doc.meta.get_field(fieldname)
    levels = set(doc.meta.get_permlevel_access(permission_type=permission))
    if permission == "read" and 0 not in levels and frappe.share.get_shared(
            doc.doctype, frappe.session.user, rights=["read"], limit=1):
        levels.add(0)
    if not (field and field.fieldtype == "Table" and field.options == childtype
            and (field.permlevel or 0) in levels):
        frappe.throw("没有读取或填写领料退库明细的权限", frappe.PermissionError)
    mask_levels = set(doc.meta.get_permlevel_access(
        permission_type="mask", user=frappe.session.user))
    if field.get("mask") and (field.permlevel or 0) not in mask_levels:
        frappe.throw("没有读取或填写领料退库明细的权限", frappe.PermissionError)
    _permitted_fields(frappe.get_meta(childtype), fields, permission, parenttype=doc.doctype)


def _lock_source(source_name):
    """Lock Flow(s), then the source voucher and its rows, using NOWAIT."""
    if not isinstance(source_name, str) or not source_name:
        _throw()
    try:
        _lock_flows(_persisted_flow_ids(source_name))
        parent = frappe.db.sql("""SELECT name FROM `tabStock Entry`
            WHERE name=%s FOR UPDATE NOWAIT""", (source_name,))
        if not parent:
            _throw()
        frappe.db.sql("""SELECT name FROM `tabStock Entry Detail`
            WHERE parent=%s AND parenttype='Stock Entry' AND parentfield='items'
            ORDER BY name FOR UPDATE NOWAIT""", (source_name,))
    except (frappe.QueryTimeoutError, frappe.QueryDeadlockError):
        frappe.throw("原领料凭证正在被处理，请稍后刷新再试", frappe.ValidationError)


def _source(source_name, *, lock=True, current=False):
    if lock:
        _lock_source(source_name)
    try:
        doc = frappe.get_doc("Stock Entry", source_name, for_update=lock or current)
    except frappe.DoesNotExistError:
        _throw()
    doc.check_permission("read")
    _permitted_fields(doc, ("company", "stock_entry_type", "purpose", "docstatus", "is_return",
                            FLOW_FIELD, "posting_date"))
    _permitted_table(doc, "items", "Stock Entry Detail", (
        "name", "idx", "item_code", "item_name", "qty", "uom", "stock_uom", "conversion_factor", "transfer_qty",
        "s_warehouse", "t_warehouse", "expense_account", "cost_center", "project", FLOW_ROW_FIELD,
        "batch_no", "serial_no", "serial_and_batch_bundle", "use_serial_batch_fields",
    ))
    if (doc.docstatus != 1 or doc.stock_entry_type != "Material Issue" or doc.purpose != "Material Issue"
            or doc.get("is_return") or not doc.company or not doc.items):
        _throw()
    if not frappe.get_list("Company", filters={"name": doc.company}, pluck="name", limit_page_length=1):
        frappe.throw("没有查看原领料公司权限", frappe.PermissionError)
    flows = {doc.get(FLOW_FIELD)} | {row.get(FLOW_ROW_FIELD) for row in doc.items}
    flows.discard(None)
    flows.discard("")
    if len(flows) > 1:
        _throw("原领料单关联了多个流程来源，不能安全生成单一来源退库。")
    if flows and doc.get(FLOW_FIELD) not in (None, "", next(iter(flows))):
        _throw()
    for flow_name in flows:
        flow = frappe.get_doc("Jifeng Flow Order", flow_name)
        flow.check_permission("read")
        _permitted_fields(flow, ("company",))
        if flow.company != doc.company:
            _throw()
    return doc


def _source_rows(source):
    result = {}
    for row in source.items:
        item = frappe.get_doc("Item", row.item_code)
        item.check_permission("read")
        _permitted_fields(item, ("stock_uom", "is_stock_item", "has_serial_no", "has_batch_no"))
        warehouse = frappe.get_doc("Warehouse", row.s_warehouse) if row.s_warehouse else None
        if warehouse:
            warehouse.check_permission("read")
            _permitted_fields(warehouse, ("company", "is_group", "disabled"))
        if (not row.get("s_warehouse") or row.get("t_warehouse") or not item.is_stock_item
                or not item.stock_uom or not warehouse or warehouse.company != source.company
                or warehouse.is_group or warehouse.disabled):
            _throw("领料退库须对应有效物料和本公司单一原发料仓库。")
        tracked = bool(item.has_serial_no or item.has_batch_no)
        if not tracked and any(row.get(field) for field in TRACKING_FIELDS):
            _throw("原物料的批次/序列设置与原领料明细不一致。")
        if not row.get("expense_account"):
            _throw("原领料行没有可确认的库存对方科目，不能自动生成退库。")
        account = frappe.get_doc("Account", row.expense_account)
        account.check_permission("read")
        _permitted_fields(account, ("company", "is_group", "disabled", "root_type"))
        if account.company != source.company or account.is_group or account.disabled or account.root_type != "Expense":
            _throw("原领料对方科目不可用于本公司退库。")
        if row.get("cost_center"):
            cost_center = frappe.get_doc("Cost Center", row.cost_center)
            cost_center.check_permission("read")
            _permitted_fields(cost_center, ("company", "is_group", "disabled"))
            if cost_center.company != source.company or cost_center.is_group or cost_center.disabled:
                _throw("原领料成本中心不可用于本公司退库。")
        quantity = _decimal(row.transfer_qty, "原领料库存数量")
        if quantity <= 0 or row.stock_uom != item.stock_uom or row.conversion_factor in (None, ""):
            _throw()
        factor = _decimal(row.conversion_factor, "原领料换算系数")
        if factor <= 0 or not _same(quantity, _decimal(row.qty, "原领料数量") * factor):
            _throw()
        result[row.name] = {"row": row, "item": item, "warehouse": warehouse,
                            "qty": quantity, "uom": item.stock_uom,
                            "flow": row.get(FLOW_ROW_FIELD) or source.get(FLOW_FIELD) or "",
                            "tracked": tracked}
    return result


def _decimal(value, label):
    try:
        result = Decimal(str(value if value not in (None, "") else 0))
    except Exception:
        _throw(f"{label}无法准确读取。")
    if not result.is_finite():
        _throw(f"{label}无法准确读取。")
    return result


def _same(left, right):
    return abs(left - right) <= TOLERANCE


def _ensure_within_source_quantities(current, submitted, source_rows):
    for detail_name, quantity in current.items():
        if submitted[detail_name] + quantity > source_rows[detail_name]["qty"] + TOLERANCE:
            frappe.throw("退库数量累计超过原领料明细的已领库存数量。", frappe.ValidationError)


def _ensure_no_pending_reposting(source, source_rows):
    """Reject valuation sources that an active native repost can still change."""
    filters = {"company": source.company, "docstatus": 1,
               "status": ["not in", ["Completed", "Skipped", "Cancelled"]]}
    fields = ["name", "docstatus", "status", "based_on", "voucher_type", "voucher_no",
              "item_code", "warehouse", "posting_date", "reposting_data_file", "items_to_be_repost"]
    jobs = frappe.get_all(REPOST, filters=filters, fields=fields,
                          order_by="name asc", limit_page_length=MAX_RELATED + 1)
    if len(jobs) > MAX_RELATED:
        _throw("库存估价重算任务过多，无法安全确定原领料价值。")
    target_pairs = {(row["row"].item_code, row["row"].s_warehouse) for row in source_rows.values()}
    for job in jobs:
        if job.get("status") not in {"Queued", "In Progress", "Failed"}:
            _throw("存在状态无法确认的库存估价重算，暂不能生成退库。")
        if (job.get("voucher_type"), job.get("voucher_no")) == ("Stock Entry", source.name):
            _throw("原领料相关库存重估尚未完成或已失败，暂不能生成退库。")
        seeds = []
        if job.get("based_on") == "Item and Warehouse":
            seeds = [job]
        elif job.get("based_on") == "Transaction":
            if job.get("reposting_data_file"):
                _throw("存在范围无法确认的库存估价重算，暂不能生成退库。")
            if job.get("items_to_be_repost"):
                try:
                    seeds = json.loads(job["items_to_be_repost"])
                except (ValueError, TypeError):
                    _throw("库存估价重算范围无法解析，暂不能生成退库。")
            if not seeds and job.get("voucher_type") and job.get("voucher_no"):
                seeds = frappe.get_all(SLE, filters={"company": source.company,
                    "voucher_type": job["voucher_type"], "voucher_no": job["voucher_no"]},
                    fields=["name", "item_code", "warehouse", "posting_date"],
                    order_by="name asc", limit_page_length=MAX_RELATED + 1)
                if len(seeds) > MAX_RELATED:
                    _throw("库存估价重算来源过多，暂不能生成退库。")
        if not isinstance(seeds, list) or not seeds:
            _throw("存在范围无法确认的库存估价重算，暂不能生成退库。")
        for seed in seeds:
            if not isinstance(seed, dict) or not seed.get("item_code") or not seed.get("warehouse"):
                _throw("库存估价重算范围不完整，暂不能生成退库。")
            pair = (seed["item_code"], seed["warehouse"])
            if pair in target_pairs:
                repost_date = seed.get("posting_date")
                if not repost_date or str(repost_date) <= str(source.posting_date):
                    _throw("相关物料仓库存在尚未完成的库存估价重算，暂不能生成退库。")


def _related(source, *, lock=False):
    """Return complete readable draft/submitted returns for one source issue."""
    query = {SOURCE_FIELD: source.name, "docstatus": ["<", 2]}
    try:
        locked_names = None
        if lock:
            locked_names = {row[0] for row in frappe.db.sql("""SELECT name FROM `tabStock Entry`
                WHERE jf_material_return_against=%s AND docstatus<2
                ORDER BY name FOR UPDATE NOWAIT""", (source.name,))}
        raw = frappe.get_all("Stock Entry", filters=query, fields=["name"], limit_page_length=MAX_RELATED + 1)
        visible = frappe.get_list("Stock Entry", filters=query, fields=["name"], limit_page_length=MAX_RELATED + 1)
    except (frappe.QueryTimeoutError, frappe.QueryDeadlockError):
        frappe.throw("相关退库凭证正在被处理，请稍后刷新再试", frappe.ValidationError)
    raw_names, visible_names = {row.name for row in raw}, {row.name for row in visible}
    if locked_names is not None and locked_names != raw_names:
        _throw("退库关联在本次读取期间已变化，请刷新后重新核对。")
    if len(raw_names) > MAX_RELATED or raw_names != visible_names:
        frappe.throw("无法完整读取原领料的退库凭证，不能计算剩余可退数量", frappe.PermissionError)
    docs = {}
    for name in sorted(visible_names):
        returned = frappe.get_doc("Stock Entry", name, for_update=lock)
        returned.check_permission("read")
        _permitted_fields(returned, ("company", "stock_entry_type", "purpose", "docstatus", SOURCE_FIELD,
                                     FLOW_FIELD, "is_return", "total_additional_costs"))
        _permitted_table(returned, "items", "Stock Entry Detail", (
            "item_code", "qty", "uom", "stock_uom", "conversion_factor", "transfer_qty",
            "s_warehouse", "t_warehouse", "expense_account", "cost_center", "project",
            FLOW_ROW_FIELD, SOURCE_ROW_FIELD, "additional_cost", "landed_cost_voucher_amount",
            "set_basic_rate_manually", "basic_rate", *TRACKING_FIELDS,
        ))
        _permitted_table(returned, "additional_costs", "Landed Cost Taxes and Charges", ("name", "amount"))
        if (returned.company != source.company or returned.stock_entry_type != "Material Receipt"
                or returned.purpose != "Material Receipt" or returned.get(SOURCE_FIELD) != source.name
                or returned.get("is_return")):
            _throw()
        docs[name] = returned
    return docs


def _no_additional_costs(doc, permission="write"):
    _permitted_fields(doc, ("total_additional_costs",), permission)
    _permitted_table(doc, "additional_costs", "Landed Cost Taxes and Charges", ("name", "amount"), permission)
    if doc.get("additional_costs") or _decimal(doc.get("total_additional_costs"), "退库额外费用") != 0:
        frappe.throw("领料退库不得增加额外费用或改变原领料价值。", frappe.ValidationError)
    for row in doc.items:
        if (_decimal(row.get("additional_cost"), "退库明细额外费用") != 0
                or _decimal(row.get("landed_cost_voucher_amount"), "退库明细分摊金额") != 0):
            frappe.throw("领料退库明细不得增加额外费用或分摊金额。", frappe.ValidationError)


def _source_rates(source, source_rows, *, current=False):
    _ensure_no_pending_reposting(source, source_rows)
    rates = {}
    for detail_name, evidence in source_rows.items():
        source_row = evidence["row"]
        filters = {"company": source.company, "voucher_type": "Stock Entry", "voucher_no": source.name,
                   "voucher_detail_no": detail_name, "item_code": source_row.item_code,
                   "warehouse": source_row.s_warehouse, "is_cancelled": 0}
        fields = ["name", "company", "docstatus", "voucher_type",
                  "voucher_no", "voucher_detail_no", "item_code", "warehouse", "stock_uom", "actual_qty",
                  "stock_value_difference", "is_cancelled", "is_adjustment_entry"]
        if evidence.get("tracked"):
            fields.extend(("serial_and_batch_bundle", "batch_no", "serial_no"))
        try:
            _permitted_query_fields(SLE, fields)
            locked_names = None
            if current:
                locked_names = {row[0] for row in frappe.db.sql("""SELECT name FROM `tabStock Ledger Entry`
                    WHERE company=%s AND voucher_type='Stock Entry' AND voucher_no=%s
                      AND voucher_detail_no=%s AND item_code=%s AND warehouse=%s AND is_cancelled=0
                    ORDER BY name LIMIT %s FOR UPDATE NOWAIT""", (
                        source.company, source.name, detail_name, source_row.item_code,
                        source_row.s_warehouse, MAX_RELATED + 1))}
            raw = frappe.get_all("Stock Ledger Entry", filters=filters, fields=fields,
                                 limit_page_length=MAX_RELATED + 1)
            visible = frappe.get_list("Stock Ledger Entry", filters=filters, fields=fields,
                                     limit_page_length=MAX_RELATED + 1)
        except (frappe.PermissionError, frappe.ValidationError):
            frappe.throw("没有完整读取原领料库存账权限，不能确定退库价值", frappe.PermissionError)
        if (len(raw) > MAX_RELATED or {row.name for row in raw} != {row.name for row in visible}
                or not visible):
            frappe.throw("原领料没有完整可读的库存价值来源，不能生成退库单", frappe.ValidationError)
        if locked_names is not None:
            if locked_names != {row.name for row in visible}:
                _throw("原领料库存价值来源在本次读取期间已变化，请刷新后核对。")
            visible = [frappe.get_doc(SLE, name, for_update=True) for name in sorted(locked_names)]
            for entry in visible:
                entry.check_permission("read")
        qty, value = Decimal(0), Decimal(0)
        for row in visible:
            if (row.company != source.company or row.docstatus != 1 or row.is_cancelled
                    or row.voucher_type != "Stock Entry" or row.voucher_no != source.name
                    or row.voucher_detail_no != detail_name or row.item_code != source_row.item_code
                    or row.warehouse != source_row.s_warehouse or row.stock_uom != evidence["uom"]):
                _throw("原领料库存价值来源与来源明细不匹配，不能自动定价。")
            actual_qty = _decimal(row.actual_qty, "原领料库存账数量")
            stock_value = _decimal(row.stock_value_difference, "原领料库存账价值")
            # Repost Item Valuation may update the source voucher's value in a
            # later dated ledger row. Include exact-voucher adjustments in the
            # net source value, while only source issue quantities count.
            if actual_qty > 0:
                _throw("原领料库存账含正向数量，无法确认净发料数量。")
            qty += -actual_qty
            value += -stock_value
        if not _same(qty, evidence["qty"]) or value < 0:
            _throw("原领料库存账数量或价值与来源明细不一致，不能自动定价。")
        rates[detail_name] = value / qty
        if evidence.get("tracked"):
            # Exact original SLE identities, never a warehouse-wide average.
            evidence["ledger"] = visible
            evidence["current"] = current
            evidence["pools"] = _original_pools(source, evidence, value)
    return rates


def _read_bundle(name, *, current=False, read=None, records=None):
    if read is not None:
        if not callable(read) or not callable(records):
            _throw("退库成本必须使用完整只读来源接口。")
        bundle = read(BUNDLE, name)
        bundle.check_permission("read")
        if bundle.name != name:
            _throw("退库批次凭证名称与准确读取来源不一致。")
        stored = records(BUNDLE_ENTRY, {"parent": name}, ENTRY_FIELDS, parenttype=BUNDLE)
        children = {row.name: row for row in bundle.get("entries") or []}
        if (not children or len(children) > MAX_RELATED or len(children) != len(bundle.get("entries") or [])
                or len(stored) != len(children) or {row.get("name") for row in stored} != set(children)):
            _throw("原批次凭证明细不完整，不能遗漏隐藏实物。")
        for row in stored:
            if (not set(ENTRY_FIELDS).issubset(row)
                    or any(children[row["name"]].get(field) != row[field] for field in ENTRY_FIELDS)):
                _throw("批次来源明细在本次读取期间已变化，请重新读取。")
        return bundle
    bundle = frappe.get_doc(BUNDLE, name, for_update=current)
    bundle.check_permission("read")
    _permitted_fields(bundle, BUNDLE_FIELDS)
    _permitted_table(bundle, "entries", BUNDLE_ENTRY, ENTRY_FIELDS)
    return bundle


def _lot_identity(evidence, serial, batch, *, read=None):
    item = evidence["item"]
    if bool(serial) != bool(item.has_serial_no) or bool(batch) != bool(item.has_batch_no):
        _throw("原领料批次/序列明细与物料追踪设置不一致。")
    if serial:
        doc = read("Serial No", serial) if read is not None else frappe.get_doc("Serial No", serial)
        doc.check_permission("read")
        if read is not None and doc.name != serial:
            _throw("原序列号名称与准确读取来源不一致。")
        if read is None:
            _permitted_fields(doc, ("item_code", "batch_no"))
        if doc.item_code != item.name or (doc.get("batch_no") or "") != (batch or ""):
            _throw("所选序列号的物料或批次与原领料不一致。")
    if batch:
        doc = read("Batch", batch) if read is not None else frappe.get_doc("Batch", batch)
        doc.check_permission("read")
        if read is not None and doc.name != batch:
            _throw("原批号名称与准确读取来源不一致。")
        if read is None:
            _permitted_fields(doc, ("item",))
        if doc.item != item.name:
            _throw("所选批次的物料与原领料不一致。")


def _bundle_pools(source, doc, row, evidence, bundle, *, original=False, allow_draft=False,
                  validate_valuation=None, allow_native_pending=False, read=None):
    draft = allow_draft and bundle.docstatus == 0
    pending_calculation = draft or allow_native_pending
    warehouse = evidence["row"].s_warehouse
    if (bundle.docstatus not in ({0, 1} if allow_draft else {1}) or bundle.get("is_cancelled")
            or bundle.company != source.company or bundle.item_code != row.item_code
            or bundle.warehouse != warehouse or bundle.voucher_type != "Stock Entry"
            or bool(bundle.get("has_serial_no")) != bool(evidence["item"].has_serial_no)
            or bool(bundle.get("has_batch_no")) != bool(evidence["item"].has_batch_no)
            or bundle.type_of_transaction != ("Outward" if original else "Inward")
            or bundle.get("voucher_no") not in ({None, "", doc.name} if draft else {doc.name})
            or bundle.get("voucher_detail_no") not in ({None, "", row.name} if draft else {row.name})):
        _throw("批次凭证的原单、原行、物料、公司、仓库或方向不一致。")
    pools = {}
    entries = bundle.get("entries") or []
    if not entries or len(entries) > MAX_RELATED:
        _throw("批次凭证没有完整可核对的实物明细。")
    for entry in entries:
        serial, batch = entry.get("serial_no"), entry.get("batch_no")
        key = ("serial", serial) if serial else ("batch", batch)
        quantity = _decimal(entry.get("qty"), "批次/序列数量")
        if (not key[1] or not quantity or (quantity < 0) != original
                or entry.get("is_cancelled") or entry.docstatus != bundle.docstatus or entry.parent != bundle.name
                or entry.parenttype != BUNDLE or entry.parentfield != "entries"
                or entry.get("item_code") not in ({None, "", row.item_code} if pending_calculation else {row.item_code})
                or entry.get("warehouse") not in ({None, "", warehouse} if pending_calculation else {warehouse})):
            _throw("批次/序列明细的数量、仓库或父凭证不一致。")
        _lot_identity(evidence, serial, batch, read=read)
        pool = pools.setdefault(key, {"qty": Decimal(0), "value": Decimal(0), "rates": set(), "batch": batch})
        if (pool["batch"] or "") != (batch or "") or serial and (pool["qty"] or abs(quantity) != 1):
            _throw("批次/序列重复、批次不一致或序列号数量不是一件。")
        pool["qty"] += abs(quantity)
        if original or (not draft if validate_valuation is None else validate_valuation):
            if entry.get("incoming_rate") in (None, "") or entry.get("stock_value_difference") in (None, ""):
                _throw("原领料或已过账退库缺少逐批/逐号历史估值。")
            rate = _decimal(entry.incoming_rate, "批次/序列原单价")
            value = _decimal(entry.stock_value_difference, "批次/序列历史价值")
            if rate < 0 or not _same(value, quantity * rate):
                _throw("批次/序列历史单价与库存价值不一致。")
            pool["value"] += abs(value)
            pool["rates"].add(rate)
    quantity = sum((pool["qty"] for pool in pools.values()), Decimal(0))
    total = abs(_decimal(bundle.get("total_qty"), "批次凭证总数量"))
    # Native allow_existing_serial_nos runs before calculate_total_qty when a
    # bundle is first inserted. The exact selected row quantity is checked below.
    if (not pending_calculation or total) and not _same(quantity, total):
        _throw("批次凭证总数量与所选实物不一致。")
    if not pending_calculation and not _same(_decimal(bundle.total_qty, "批次凭证总数量"),
                                             -quantity if original else quantity):
        _throw("批次凭证总数量方向与逐批/逐号实物不一致。")
    if original or not pending_calculation and (not draft if validate_valuation is None else validate_valuation):
        value = sum((pool["value"] for pool in pools.values()), Decimal(0))
        if bundle.get("total_amount") in (None, "") or not _same(-value if original else value,
                                                               _decimal(bundle.total_amount, "批次凭证总价值")):
            _throw("批次凭证总价值与逐批/逐号历史价值不一致。")
    return pools


def _original_pools(source, evidence, value, *, read=None, records=None):
    row = evidence["row"]
    ledger = evidence["ledger"]
    bundles = {entry.get("serial_and_batch_bundle") for entry in ledger if _decimal(entry.actual_qty, "原领料数量") < 0}
    bundles.discard(None); bundles.discard("")
    pools = {}
    if bundles:
        if any(_decimal(entry.actual_qty, "原领料数量") < 0 and not entry.get("serial_and_batch_bundle") for entry in ledger):
            _throw("原领料同时存在未关联批次凭证的发料库存账。")
        if row.get("serial_and_batch_bundle") and row.serial_and_batch_bundle not in bundles:
            _throw("原领料行的批次凭证与有效库存账不一致。")
        filters = {"company": source.company, "voucher_type": "Stock Entry", "voucher_no": source.name,
                   "voucher_detail_no": row.name, "item_code": row.item_code, "warehouse": row.s_warehouse,
                   "docstatus": 1, "is_cancelled": 0, "type_of_transaction": "Outward"}
        if read is None:
            _permitted_query_fields(BUNDLE, ("name", *filters.keys()))
            raw = frappe.get_all(BUNDLE, filters=filters, pluck="name", limit_page_length=MAX_RELATED + 1)
            visible = frappe.get_list(BUNDLE, filters=filters, pluck="name", limit_page_length=MAX_RELATED + 1)
        else:
            if not callable(records):
                _throw("退库成本必须使用完整只读来源接口。")
            rows = records(BUNDLE, filters, BUNDLE_FIELDS)
            if any(not set(BUNDLE_FIELDS).issubset(entry) for entry in rows):
                _throw("原领料批次字段来源不完整。")
            raw = visible = [entry["name"] for entry in rows]
        if len(raw) > MAX_RELATED or set(raw) != set(visible):
            _throw("无法完整读取原领料批次凭证，不能核对退库额度。", frappe.PermissionError)
        if set(raw) != bundles or len(raw) != len(bundles):
            _throw("原领料有效批次凭证与库存账来源不完整。")
        for name in sorted(bundles):
            bundle = _read_bundle(name, current=evidence.get("current", False), read=read, records=records)
            if read is not None and any(bundle.get(field) != next(entry for entry in rows if entry["name"] == name)[field]
                                        for field in BUNDLE_FIELDS):
                _throw("原领料批次凭证在本次读取期间已变化，请重新读取。")
            selected = _bundle_pools(source, source, row, evidence,
                                     bundle, original=True, read=read)
            # Each original outward bundle must equal its own exact SLEs.
            # Equal whole-row totals cannot conceal swapped prices between
            # batches/bundles, even when they share one source detail.
            entries = [entry for entry in ledger if entry.get("serial_and_batch_bundle") == name
                       and _decimal(entry.actual_qty, "原领料数量") < 0]
            if (not _same(sum((pool["qty"] for pool in selected.values()), Decimal(0)),
                          -sum((_decimal(entry.actual_qty, "原批出库数量") for entry in entries), Decimal(0)))
                    or not _same(sum((pool["value"] for pool in selected.values()), Decimal(0)),
                                 -sum((_decimal(entry.stock_value_difference, "原批出库金额") for entry in entries), Decimal(0)))):
                _throw("原批次凭证数量或价值与其准确出库库存账不一致。")
            for key, pool in selected.items():
                old = pools.setdefault(key, {"qty": Decimal(0), "value": Decimal(0), "rates": set(), "batch": pool["batch"]})
                if (old["batch"] or "") != (pool["batch"] or "") or key[0] == "serial" and old["qty"]:
                    _throw("原领料序列重复或批次来源不一致。")
                old["qty"] += pool["qty"]; old["value"] += pool["value"]; old["rates"].update(pool["rates"])
    else:
        # Legacy tracking is only usable when the exact original ledger proves
        # one batch or one serial. Never infer per-serial prices from a row average.
        serials = _serials(row.get("serial_no"))
        batch = row.get("batch_no")
        if len(serials) > 1 or not serials and not batch:
            _throw("原领料缺少逐批/逐号历史凭证，不能用整行均价推测退库。")
        serial = serials[0] if serials else None
        _lot_identity(evidence, serial, batch, read=read)
        if serial and evidence["qty"] != 1:
            _throw("原序列号领料数量不是一件，不能核对历史估值。")
        key = ("serial", serial) if serial else ("batch", batch)
        rates = set()
        for entry in ledger:
            if _decimal(entry.actual_qty, "原领料数量") < 0:
                if _serials(entry.get("serial_no")) != serials or (entry.get("batch_no") or "") != (batch or ""):
                    _throw("原领料旧批次/序列字段与有效库存账不一致。")
                rates.add(_decimal(entry.stock_value_difference, "原领料价值") / _decimal(entry.actual_qty, "原领料数量"))
        # Exact-voucher adjustment may change the sole proven physical pool.
        pools[key] = {"qty": evidence["qty"], "value": value, "rates": rates, "batch": batch}
    if (not _same(sum((pool["qty"] for pool in pools.values()), Decimal(0)), evidence["qty"])
            or not _same(sum((pool["value"] for pool in pools.values()), Decimal(0)), value)):
        _throw("原批次/序列数量或历史价值与有效领料库存账不一致。")
    if (row.get("serial_no") and set(_serials(row.serial_no)) != {key[1] for key in pools if key[0] == "serial"}
            or row.get("batch_no") and {pool["batch"] for pool in pools.values()} != {row.batch_no}):
        _throw("原领料旧批次/序列字段与准确原实物凭证不一致。")
    if evidence["item"].has_serial_no:
        if read is None:
            _permitted_fields(evidence["item"], ("use_serial_no_wise_valuation",))
        if not evidence["item"].get("use_serial_no_wise_valuation"):
            # In this native mode StockLedger bypasses SABB valuation and an
            # inward Stock Entry uses its row incoming_rate. It can still return
            # an explicitly selected serial at its exact issue price when every
            # original serial proves the same price and the SLE proves that
            # whole row value. Different serial prices cannot be inferred from
            # an aggregate SLE merely because their average happens to match.
            rate = value / evidence["qty"]
            if any(len(pool["rates"]) != 1 or not _same(pool["value"] / pool["qty"], rate)
                   or any(not _same(price, rate) for price in pool["rates"])
                   for pool in pools.values()):
                _throw("该物料未启用逐号估值且原行序列价格不一致，不能唯一确认部分退库原金额。")
            evidence["serial_row_valuation"] = True
    evidence["original_bundles"] = sorted(bundles)
    return pools


def _serials(value):
    if value is not None and not isinstance(value, str):
        _throw("序列号格式无法核对。")
    serials = [part.strip() for part in (value or "").splitlines() if part.strip()]
    if len(serials) != len(set(serials)) or len(serials) > MAX_RELATED:
        _throw("明确选择的序列号重复或数量过多。")
    return serials


def _selected_pools(source, doc, row, evidence, *, bundle=None, allow_draft=False,
                    validate_valuation=None, allow_native_pending=False, read=None, records=None):
    if bundle is None and row.get("serial_and_batch_bundle"):
        bundle = _read_bundle(row.serial_and_batch_bundle, read=read, records=records)
    if bundle is not None:
        pools = _bundle_pools(source, doc, row, evidence, bundle, allow_draft=allow_draft,
                              validate_valuation=validate_valuation, allow_native_pending=allow_native_pending, read=read)
        # Legacy fields may mirror a native bundle, but may not contradict it.
        serials = _serials(row.get("serial_no"))
        if serials and set(serials) != {key[1] for key in pools if key[0] == "serial"}:
            _throw("序列号字段与所选原生批次凭证不一致。")
        if row.get("batch_no") and {pool["batch"] for pool in pools.values()} != {row.batch_no}:
            _throw("批号字段与所选原生批次凭证不一致。")
    else:
        serials, batch = _serials(row.get("serial_no")), row.get("batch_no")
        if not serials and not batch:
            return {}
        quantity = _decimal(row.transfer_qty, "退库库存数量")
        if serials and quantity != len(serials):
            _throw("明选序列号数量与退库库存数量不一致。")
        pools = {("serial", serial): {"qty": Decimal(1), "batch": batch, "rates": set(), "value": Decimal(0)} for serial in serials}
        if not serials:
            pools[("batch", batch)] = {"qty": quantity, "batch": batch, "rates": set(), "value": Decimal(0)}
        if not row.get("use_serial_batch_fields"):
            _throw("手工填写批次/序列号时须启用原生批次序列字段，不能自动换成其他实物。")
    if not _same(sum((pool["qty"] for pool in pools.values()), Decimal(0)), _decimal(row.transfer_qty, "退库库存数量")):
        _throw("明选批次/序列数量与退库库存数量不一致。")
    return pools


def _selected_value(evidence, chosen, used, *, posted=False):
    value = Decimal(0)
    for key, current in chosen.items():
        original = evidence["pools"].get(key)
        if not original or (original["batch"] or "") != (current["batch"] or "") or current["qty"] <= 0:
            _throw("所选批次/序列号不属于准确原领料行。")
        total = used.get(key, Decimal(0)) + current["qty"]
        if total > original["qty"] + TOLERANCE:
            _throw("该原领料行的批次/序列号累计退回超过原数量。")
        # A nonserialized batch is an interchangeable pool of this exact issue
        # row. Its return unit value is its proven issued value / quantity,
        # never a guessed physical layer or the warehouse's current average.
        # Serialized lots remain one uniquely identified original unit each.
        rate = original["value"] / original["qty"]
        if posted and (not current["rates"] or any(not _same(price, rate) for price in current["rates"])
                       or not _same(current["value"], current["qty"] * rate)):
            _throw("已过账退库的逐批/逐号价值与原领料不一致。")
        amount = current["value"] if posted else current["qty"] * rate
        prior_value = (used.values_used[key] if isinstance(used, MaterialReturnUsage)
                       else used.get(key, Decimal(0)) * rate)
        if prior_value + amount > original["value"] + TOLERANCE:
            _throw("该原领料行的批次/序列号累计退回金额超过原出库价值。")
        value += amount
        used[key] = total
        if isinstance(used, MaterialReturnUsage):
            used.values_used[key] = prior_value + amount
    return value


def _submitted_lots(source, source_rows, docs, current_name):
    used = defaultdict(MaterialReturnUsage)
    for returned in docs.values():
        if returned.docstatus != 1 or returned.name == current_name:
            continue
        for row in returned.items:
            evidence = source_rows.get(row.get(SOURCE_ROW_FIELD))
            if not evidence:
                _throw()
            if evidence.get("tracked"):
                chosen = _selected_pools(source, returned, row, evidence)
                if not chosen:
                    _throw("已提交退库缺少明确批次/序列号。")
                value = _selected_value(evidence, chosen, used[row.get(SOURCE_ROW_FIELD)], posted=bool(row.get("serial_and_batch_bundle")))
                if not row.get("set_basic_rate_manually") or not _same(value, _decimal(row.transfer_qty, "退库数量") * _decimal(row.basic_rate, "退库单价")):
                    _throw("已提交退库金额与选中的原领料实物不一致。")
    return used


def material_issue_evidence(source, row, item, ledger, *, read, records):
    """Pure read graph of one exact tracked issue row and its original lots.

    The caller owns full document/field permissions, pending-repost checks and
    read-change closure, including every callback enumeration. No writer APIs
    or quantity allocation are invoked here.
    """
    quantity = _decimal(row.get("transfer_qty"), "原领料库存数量")
    if (not callable(read) or not callable(records) or source.docstatus != 1
            or source.get("stock_entry_type") != "Material Issue" or source.get("purpose") != "Material Issue"
            or source.get("is_return") or not source.get("company") or item.name != row.item_code
            or not item.get("is_stock_item") or quantity <= 0 or not row.get("s_warehouse") or row.get("t_warehouse")
            or row.get("stock_uom") != item.get("stock_uom") or not (item.get("has_batch_no") or item.get("has_serial_no"))):
        _throw()
    value, actual_qty = Decimal(0), Decimal(0)
    for entry in ledger:
        if (entry.get("company") != source.company or entry.get("docstatus") != 1 or entry.get("is_cancelled")
                or entry.get("voucher_type") != "Stock Entry" or entry.get("voucher_no") != source.name
                or entry.get("voucher_detail_no") != row.name or entry.get("item_code") != row.item_code
                or entry.get("warehouse") != row.s_warehouse or entry.get("stock_uom") != item.stock_uom):
            _throw("原领料库存价值来源与来源明细不匹配，不能自动定价。")
        moved = _decimal(entry.get("actual_qty"), "原领料库存数量")
        if moved > 0 or not moved and not entry.get("is_adjustment_entry"):
            _throw("原领料库存账方向或调整来源不一致。")
        actual_qty -= moved
        value -= _decimal(entry.get("stock_value_difference"), "原领料库存金额")
    if not ledger or not _same(actual_qty, quantity) or value < 0:
        _throw("原领料库存账数量或价值与来源明细不一致，不能自动定价。")
    evidence = {"row": row, "item": item, "qty": quantity, "uom": item.stock_uom, "tracked": True,
                "flow": row.get(FLOW_ROW_FIELD) or source.get(FLOW_FIELD) or "", "ledger": ledger}
    evidence["pools"] = _original_pools(source, evidence, value, read=read, records=records)
    return evidence


def material_return_value(evidence, source, doc, row, ledger, *, read, records, used):
    """Prove posted lot values and cumulative qty/value, with read callbacks only."""
    if not isinstance(used, MaterialReturnUsage):
        _throw("退库成本须完整累计原实物数量和已过账金额。")
    if (doc.get("docstatus") != 1 or doc.get(SOURCE_FIELD) != source.name or doc.get("company") != source.company
            or doc.get("stock_entry_type") != "Material Receipt" or doc.get("purpose") != "Material Receipt"
            or doc.get("is_return") or doc.get("additional_costs")
            or _decimal(doc.get("total_additional_costs"), "退库追加费用")
            or _decimal(row.get("additional_cost"), "退库追加费用")
            or _decimal(row.get("landed_cost_voucher_amount"), "退库分摊费用")):
        _throw()
    _validate_return_row(source, doc, row, evidence, None)
    chosen = _selected_pools(source, doc, row, evidence, read=read, records=records)
    if not chosen:
        _throw("已提交退库缺少明确批次/序列号。")
    quantity, actual_value = Decimal(0), Decimal(0)
    for entry in ledger:
        if (entry.get("company") != source.company or entry.get("docstatus") != 1 or entry.get("is_cancelled")
                or entry.get("voucher_type") != "Stock Entry" or entry.get("voucher_no") != doc.name
                or entry.get("voucher_detail_no") != row.name or entry.get("item_code") != row.item_code
                or entry.get("warehouse") != row.t_warehouse or entry.get("stock_uom") != evidence["uom"]):
            _throw("退库实际库存账与准确原行、公司或原仓库不一致。")
        moved = _decimal(entry.get("actual_qty"), "退库库存数量")
        if moved < 0 or not moved and not entry.get("is_adjustment_entry"):
            _throw("退库实际库存账方向或调整来源不一致。")
        quantity += moved
        actual_value += _decimal(entry.get("stock_value_difference"), "退库库存金额")
        if moved and (row.get("serial_and_batch_bundle") or None) != (entry.get("serial_and_batch_bundle") or None):
            _throw("退库库存账没有准确关联所选批次凭证。")
        if moved and ((entry.get("serial_no") or not row.get("serial_and_batch_bundle"))
                      and set(_serials(entry.get("serial_no"))) != {key[1] for key in chosen if key[0] == "serial"}
                      or (entry.get("batch_no") or not row.get("serial_and_batch_bundle"))
                      and {pool["batch"] or None for pool in chosen.values()} != {entry.get("batch_no") or None}):
            _throw("退库实际库存账的批次/序列号与所选原实物不一致。")
    if not ledger or quantity <= 0 or actual_value < 0 or not _same(quantity, _decimal(row.transfer_qty, "退库数量")):
        _throw("退库实际库存账数量或金额不完整。")
    posted = bool(row.get("serial_and_batch_bundle"))
    if not posted:
        # One legacy lot has an exact ledger value. A row spanning several
        # differently priced serials needs native per-serial return evidence.
        if len(chosen) != 1:
            _throw("多实物退库缺少逐号实际库存价值凭证，不能按整行金额猜价。")
        current = next(iter(chosen.values()))
        current["value"] = actual_value
        current["rates"] = {_decimal(row.get("basic_rate"), "退库原单价")}
        posted = True
    candidate = used.copy()
    value = _selected_value(evidence, chosen, candidate, posted=posted)
    if (not _same(value, actual_value) or not row.get("set_basic_rate_manually")
            or not _same(value, quantity * _decimal(row.get("basic_rate"), "退库原单价"))):
        _throw("退库实际库存金额与所选原领料实物价值不一致。")
    used.clear()
    used.update(candidate)
    used.values_used.clear()
    used.values_used.update(candidate.values_used)
    return actual_value


def _submitted_by_detail(source, source_rows, docs):
    totals = defaultdict(Decimal)
    for returned in docs.values():
        _no_additional_costs(returned, permission="read")
        for row in returned.items:
            detail_name = row.get(SOURCE_ROW_FIELD)
            if detail_name not in source_rows:
                _throw("退库单包含无法关联原领料明细的行。")
            _validate_return_row(source, returned, row, source_rows[detail_name], None)
            if returned.docstatus == 1:
                totals[detail_name] += _decimal(row.transfer_qty, "退库库存数量")
    return totals


def _submitted_by_other_returns(source, source_rows, docs, current_name):
    """Count posted returns other than the document being validated.

    A Stock Entry being updated after submit is returned by _related() as a
    submitted document. Its quantity is added separately as the current
    document below, so including it here would count it twice.
    """
    others = {name: returned for name, returned in docs.items() if name != current_name}
    return _submitted_by_detail(source, source_rows, others)


def _return_validation_state(doc, previous):
    """Return whether this is an active validation or an ordinary cancellation."""
    status = int(doc.get("docstatus") or 0)
    action = getattr(doc, "_action", None) or doc.get("_action")
    if status == 0:
        return "active"
    if status == 1 and action in ("submit", "update_after_submit"):
        return "active"
    if (status == 2 and action == "cancel" and previous
            and int(previous.get("docstatus") or 0) == 1):
        return "cancel"
    frappe.throw("领料退库只能保存草稿、提交或按原生流程取消。", frappe.ValidationError)


def submitted_return_quantities_for_flow(flow_name, issue_names):
    """Return posted quantities by item after checking readable source links."""
    if not issue_names:
        return defaultdict(Decimal)
    try:
        names = frappe.db.sql("""SELECT name FROM `tabStock Entry`
            WHERE jf_material_issue_flow=%s AND docstatus=1
              AND stock_entry_type='Material Receipt' AND purpose='Material Receipt'
              AND jf_material_return_against IS NOT NULL AND jf_material_return_against!=''
            ORDER BY name LIMIT %s FOR UPDATE NOWAIT""", (flow_name, MAX_RELATED + 1), as_dict=True)
    except (frappe.QueryTimeoutError, frappe.QueryDeadlockError):
        frappe.throw("相关领料退库正在被核对，请稍后重试", frappe.ValidationError)
    if len(names) > MAX_RELATED:
        frappe.throw("相关领料退库数量过多，无法安全核对净领料。", frappe.ValidationError)
    totals = defaultdict(Decimal)
    known_issues = set(issue_names)
    by_source = defaultdict(dict)
    for result in names:
        name = result.name
        try:
            returned = frappe.get_doc("Stock Entry", name, for_update=True)
            returned.check_permission("read")
        except frappe.PermissionError:
            frappe.throw("没有读取流程退库凭证的权限，不能计算净领料。", frappe.PermissionError)
        _permitted_fields(returned, ("company", "stock_entry_type", "purpose", "docstatus", "is_return",
                                     SOURCE_FIELD, FLOW_FIELD))
        _permitted_table(returned, "items", "Stock Entry Detail", (
            "name", "item_code", "qty", "uom", "stock_uom", "conversion_factor", "transfer_qty",
            "s_warehouse", "t_warehouse", "expense_account", "cost_center", "project",
            FLOW_ROW_FIELD, SOURCE_ROW_FIELD, "basic_rate", "set_basic_rate_manually",
            "additional_cost", "landed_cost_voucher_amount", *TRACKING_FIELDS,
        ))
        source_name = returned.get(SOURCE_FIELD)
        if (returned.docstatus != 1 or returned.get("is_return") or returned.get(FLOW_FIELD) != flow_name
                or source_name not in known_issues):
            _throw("流程存在无法核实来源的已提交领料退库，不能计算净领料。")
        by_source[source_name][returned.name] = returned
    for source_name, returned_docs in by_source.items():
        try:
            source = _source(source_name, lock=False)
        except frappe.PermissionError:
            frappe.throw("没有读取流程原领料凭证的权限，不能计算净领料。", frappe.PermissionError)
        if any(source.company != doc.company for doc in returned_docs.values()):
            _throw()
        source_rows = _source_rows(source)
        if any(evidence.get("tracked") for evidence in source_rows.values()):
            _source_rates(source, {name: evidence for name, evidence in source_rows.items() if evidence.get("tracked")})
            _submitted_lots(source, source_rows, returned_docs, None)
        returned_by_detail = _submitted_by_detail(source, source_rows, returned_docs)
        _ensure_within_source_quantities(returned_by_detail, defaultdict(Decimal), source_rows)
        for detail_name, quantity in returned_by_detail.items():
            totals[source_rows[detail_name]["row"].item_code] += quantity
    return totals


def _validate_return_row(source, doc, row, evidence, rate):
    source_row = evidence["row"]
    if (row.get(SOURCE_ROW_FIELD) != source_row.name or row.item_code != source_row.item_code
            or row.stock_uom != evidence["uom"] or row.uom != evidence["uom"]
            or not _same(_decimal(row.conversion_factor, "退库换算系数"), Decimal(1))
            or row.s_warehouse or row.t_warehouse != source_row.s_warehouse
            or row.get(FLOW_ROW_FIELD) != evidence["flow"]
            or row.get("expense_account") != source_row.get("expense_account")
            or row.get("cost_center") != source_row.get("cost_center")
            or row.get("project") != source_row.get("project")):
        _throw("退库明细必须关联原领料行，并退回原发料仓。")
    quantity = _decimal(row.transfer_qty, "退库库存数量")
    if quantity <= 0 or not _same(quantity, _decimal(row.qty, "退库数量")):
        _throw("退库数量必须以原物料库存单位填写且大于零。")
    if not evidence.get("tracked") and any(row.get(field) for field in TRACKING_FIELDS):
        _throw("非批次/序列原领料行不能改为其他实物来源。")
    if rate is not None:
        if not row.get("set_basic_rate_manually") or not _same(
                _decimal(row.basic_rate, "退库单位价值"), rate):
            _throw("退库单位价值必须等于原领料库存账实际单位价值。")


def validate_material_return(doc):
    """Validate normal save/submit of an exact, source-bound Material Receipt."""
    source_name = doc.get(SOURCE_FIELD)
    if not source_name:
        frappe.throw("领料退库单必须关联原领料凭证。", frappe.ValidationError)
    if doc.doctype != "Stock Entry":
        _throw()
    previous = doc.get_doc_before_save() if not doc.is_new() else None
    state = _return_validation_state(doc, previous)
    if previous and previous.get(SOURCE_FIELD) != source_name:
        frappe.throw("退库草稿的原领料来源不可更改。", frappe.ValidationError)
    _lock_source(source_name)
    source = _source(source_name, lock=False, current=True)
    doc.check_permission("create" if doc.is_new() else "write")
    _permitted_fields(doc, ("company", "stock_entry_type", "purpose", SOURCE_FIELD, FLOW_FIELD), "write")
    _permitted_table(doc, "items", "Stock Entry Detail", (
        "item_code", "qty", "uom", "stock_uom", "conversion_factor", "transfer_qty",
        "s_warehouse", "t_warehouse", "basic_rate", "set_basic_rate_manually", "expense_account",
        "cost_center", "project", FLOW_ROW_FIELD, SOURCE_ROW_FIELD, "additional_cost",
        "landed_cost_voucher_amount", "allow_zero_valuation_rate", *TRACKING_FIELDS,
    ), "write")
    if (doc.company != source.company or doc.stock_entry_type != "Material Receipt"
            or doc.purpose != "Material Receipt" or doc.get("is_return") or not doc.items):
        _throw("领料退库必须为同公司Material Receipt并有明细。")
    # Cancellation must remain possible through the native Stock Entry path.
    # The canceled document is not part of the active cumulative quantity and
    # needs no fresh valuation/cap check to reverse its own ledger entries.
    if state == "cancel":
        return True
    _no_additional_costs(doc)
    source_rows = _source_rows(source)
    rates = _source_rates(source, source_rows, current=True)
    related = _related(source, lock=True)
    if len([value for value in related.values() if value.docstatus == 0 and value.name != doc.name]) > 0:
        frappe.throw("原领料已有另一张退库草稿，请先核对或删除后再保存。", frappe.ValidationError)
    submitted = _submitted_by_other_returns(source, source_rows, related, doc.name)
    used_lots = _submitted_lots(source, source_rows, related, doc.name)
    current = defaultdict(Decimal)
    seen = set()
    for row in doc.items:
        detail_name = row.get(SOURCE_ROW_FIELD)
        evidence = source_rows.get(detail_name)
        if not evidence or detail_name in seen:
            _throw("每条退库明细必须唯一关联一条原领料明细。")
        seen.add(detail_name)
        _validate_return_row(source, doc, row, evidence, None if evidence.get("tracked") else rates[detail_name])
        if evidence.get("tracked"):
            choosing = doc.docstatus == 0 or doc.get("_action") == "submit" or getattr(doc, "_action", None) == "submit"
            chosen = _selected_pools(source, doc, row, evidence, allow_draft=choosing)
            if not chosen:
                if doc.docstatus != 0:
                    _throw("请在原生批次/序列选择器明选原领料实物后提交，不能自动选批或编号。")
                expected = Decimal(0)
            else:
                value = _selected_value(evidence, chosen, used_lots[detail_name], posted=not choosing and bool(row.get("serial_and_batch_bundle")))
                expected = value / _decimal(row.transfer_qty, "退库数量")
            if choosing:
                # The only average here is the total of the explicitly selected
                # historical pools, for the native receipt row/GL. Bundle entry
                # rates remain the individual historical prices via the mixin.
                row.set_basic_rate_manually = 1
                row.basic_rate = float(expected)
                row.allow_zero_valuation_rate = int(expected == 0)
            elif not row.get("set_basic_rate_manually") or not _same(_decimal(row.basic_rate, "退库单价"), expected):
                _throw("退库单位价值与所选原领料批次/序列历史价值不一致。")
        current[detail_name] += _decimal(row.transfer_qty, "退库库存数量")
    _ensure_within_source_quantities(current, submitted, source_rows)
    flow_values = {source.get(FLOW_FIELD)} | {row.get(FLOW_ROW_FIELD) for row in source.items}
    flow_values.discard(None)
    flow_values.discard("")
    if len(flow_values) == 1:
        expected_flow = next(iter(flow_values))
        if doc.get(FLOW_FIELD) not in (None, "", expected_flow):
            _throw()
        doc.set(FLOW_FIELD, expected_flow)
    elif len(flow_values) > 1:
        doc.set(FLOW_FIELD, "")
    return True


@frappe.whitelist(methods=["POST"])
def make_material_return(source_name):
    """Create or reuse one editable Material Receipt draft from an issue."""
    if frappe.session.user == "Guest":
        frappe.throw("请登录后创建领料退库", frappe.PermissionError)
    frappe.has_permission("Stock Entry", "create", throw=True)
    source = _source(source_name, lock=True)
    source.check_permission("read")
    source_rows = _source_rows(source)
    rates = _source_rates(source, source_rows, current=True)
    related = _related(source, lock=True)
    drafts = [doc for doc in related.values() if doc.docstatus == 0]
    if len(drafts) > 1:
        frappe.throw("原领料已有多张退库草稿，请先核对。", frappe.ValidationError)
    if drafts:
        validate_material_return(drafts[0])
        return {"name": drafts[0].name, "docstatus": 0, "reused": True}
    submitted = _submitted_by_detail(source, source_rows, related)
    items = []
    for detail_name, evidence in source_rows.items():
        remaining = evidence["qty"] - submitted[detail_name]
        if remaining <= TOLERANCE:
            continue
        source_row = evidence["row"]
        row = {
            "item_code": source_row.item_code, "item_name": source_row.item_name,
            "qty": str(remaining), "uom": evidence["uom"], "stock_uom": evidence["uom"],
            "conversion_factor": 1, "t_warehouse": source_row.s_warehouse,
            "expense_account": source_row.get("expense_account"),
            "cost_center": source_row.get("cost_center"), "project": source_row.get("project"),
            FLOW_ROW_FIELD: evidence["flow"], SOURCE_ROW_FIELD: detail_name,
            "set_basic_rate_manually": 1, "basic_rate": str(0 if evidence.get("tracked") else rates[detail_name]),
        }
        if evidence.get("tracked"):
            # Native selectors remain available in the editable receipt. Never
            # copy the original whole bundle or auto-select its first entries.
            row.update(use_serial_batch_fields=1, allow_zero_valuation_rate=1)
        items.append(row)
    if not items:
        frappe.throw("原领料明细均已退回，没有可生成的剩余数量。", frappe.ValidationError)
    # Frappe may mutate child dictionaries passed to get_doc by injecting
    # framework fields such as `doctype`; permission checks must cover only the
    # business fields selected above, not that construction metadata.
    item_write_fields = tuple(sorted({field for row in items for field in row}))
    flow_values = {source.get(FLOW_FIELD)} | {row.get(FLOW_ROW_FIELD) for row in source.items}
    flow_values.discard(None)
    flow_values.discard("")
    header_flow = next(iter(flow_values)) if len(flow_values) == 1 else ""
    draft = frappe.get_doc({
        "doctype": "Stock Entry", "company": source.company,
        "stock_entry_type": "Material Receipt", "purpose": "Material Receipt",
        SOURCE_FIELD: source.name, FLOW_FIELD: header_flow, "items": items,
    })
    draft.check_permission("create")
    _permitted_fields(draft, ("company", "stock_entry_type", "purpose", SOURCE_FIELD, FLOW_FIELD), "write")
    _permitted_table(draft, "items", "Stock Entry Detail", item_write_fields, "write")
    draft.insert()
    validate_material_return(draft)
    return {"name": draft.name, "docstatus": 0, "reused": False}


@frappe.whitelist(methods=["GET"])
def get_material_return_sources(source_name):
    """Read the exact original pools, never create or allocate a selection."""
    if frappe.session.user == "Guest":
        _throw("请登录后查看原领料退库来源", frappe.PermissionError)
    source = _source(source_name, lock=False)
    source_rows = _source_rows(source)
    _source_rates(source, source_rows)
    related = _related(source)
    submitted = _submitted_by_detail(source, source_rows, related)
    _ensure_within_source_quantities(submitted, defaultdict(Decimal), source_rows)
    used = _submitted_lots(source, source_rows, related, None)
    rows = []
    for name, evidence in source_rows.items():
        row = evidence["row"]
        lots = [{"serial_no": key[1] if key[0] == "serial" else "", "batch_no": pool["batch"] or "",
                 "original_qty": str(pool["qty"]), "returned_qty": str(used[name].get(key, Decimal(0))),
                 "remaining_qty": str(pool["qty"] - used[name].get(key, Decimal(0)))}
                for key, pool in sorted(evidence.get("pools", {}).items())]
        rows.append({"source_detail": name, "source_idx": row.idx, "item_code": row.item_code,
                     "stock_uom": evidence["uom"], "warehouse": row.s_warehouse,
                     "remaining_qty": str(evidence["qty"] - submitted[name]),
                     "original_bundles": evidence.get("original_bundles", []), "lots": lots})
    return {"source_name": source.name, "rows": rows}


def _material_bundle_context(bundle, parent=None):
    """Authorize one exact source return before native bundle processing."""
    if bundle.get("voucher_type") != "Stock Entry" or not bundle.get("voucher_no"):
        return None
    doc = parent if parent is not None else frappe.get_doc("Stock Entry", bundle.voucher_no)
    if doc.doctype != "Stock Entry" or doc.name != bundle.voucher_no:
        _throw()
    source_name = doc.get(SOURCE_FIELD)
    if not source_name or doc.docstatus == 2:
        return None
    doc.check_permission("read")
    _permitted_fields(doc, ("company", "docstatus", "stock_entry_type", "purpose", "is_return", SOURCE_FIELD, FLOW_FIELD))
    _lock_source(source_name)
    if parent is None:
        doc = frappe.get_doc("Stock Entry", bundle.voucher_no, for_update=True)
        doc.check_permission("read")
        if doc.get(SOURCE_FIELD) != source_name or doc.docstatus == 2:
            _throw("退库来源在批次凭证读取期间已变化，请刷新后核对。")
    _permitted_table(doc, "items", "Stock Entry Detail", (
        "name", "item_code", "qty", "transfer_qty", "stock_uom", "uom", "conversion_factor",
        "s_warehouse", "t_warehouse", "expense_account", "cost_center", "project", FLOW_ROW_FIELD,
        SOURCE_ROW_FIELD, "set_basic_rate_manually", "basic_rate", *TRACKING_FIELDS,
    ))
    if doc.stock_entry_type != "Material Receipt" or doc.purpose != "Material Receipt" or doc.get("is_return"):
        _throw("原领料批次退库只允许准确来源的 Material Receipt。")
    row = next((row for row in doc.items if row.name == bundle.get("voucher_detail_no")), None)
    if not row or row.get("serial_and_batch_bundle") not in (None, "", bundle.name):
        _throw("退库批次凭证没有准确关联本次退库明细。")
    source = _source(source_name, lock=False, current=True)
    if doc.company != source.company:
        _throw()
    source_rows = _source_rows(source)
    _source_rates(source, source_rows, current=True)
    evidence = source_rows.get(row.get(SOURCE_ROW_FIELD))
    if not evidence or not evidence.get("tracked"):
        _throw("退库批次凭证没有准确的原领料实物来源。")
    _validate_return_row(source, doc, row, evidence, None)
    bundle.check_permission("create" if bundle.is_new() else "read")
    _permitted_fields(bundle, BUNDLE_FIELDS)
    _permitted_table(bundle, "entries", BUNDLE_ENTRY, ENTRY_FIELDS)
    related = _related(source, lock=True)
    totals = _submitted_by_other_returns(source, source_rows, related, doc.name)
    current = defaultdict(Decimal)
    for item_row in doc.items:
        detail = item_row.get(SOURCE_ROW_FIELD)
        if detail not in source_rows:
            _throw()
        current[detail] += _decimal(item_row.transfer_qty, "本次退库数量")
    _ensure_within_source_quantities(current, totals, source_rows)
    used = _submitted_lots(source, source_rows, related, doc.name)
    # Native validate() enters this callback before set_incoming_rate() on a
    # new submission. docstatus=1 alone does not mean that its original per-lot
    # rates have already been calculated. Authorize identities and quantities
    # now; the mixin checks the prices after the native calculation below.
    chosen = _selected_pools(source, doc, row, evidence, bundle=bundle,
                             allow_draft=bundle.docstatus == 0, validate_valuation=False,
                             allow_native_pending=True)
    if not chosen:
        _throw("请明确选择准确原领料批次或序列号，不能自动选号。")
    prior = used[row.get(SOURCE_ROW_FIELD)]
    evidence["prior_return_usage"] = prior.copy()
    _selected_value(evidence, chosen, prior)
    return source, doc, row, evidence


class MaterialReturnBundleMixin:
    def set_incoming_rate(self, parent=None, row=None, save=False, allow_negative_stock=False,
                          prev_sle=None):
        context = _material_bundle_context(self, parent)
        serial_row_valuation = bool(context and context[3].get("serial_row_valuation"))
        if serial_row_valuation:
            # Native return rate calculation deliberately skips serialized
            # entries in this mode. Preserve the exact uniform historical rate
            # in its physical bundle as well as the separately validated receipt
            # row; no global Item/Stock Settings changes or auto-selection.
            for entry in self.entries:
                pool = context[3]["pools"][("serial", entry.serial_no)]
                rate = pool["value"] / pool["qty"]
                entry.incoming_rate = float(rate)
                entry.stock_value_difference = float(_decimal(entry.qty, "退库序列数量") * rate)
        result = super().set_incoming_rate(parent=parent, row=row, save=save,
            allow_negative_stock=allow_negative_stock, prev_sle=prev_sle)
        if context:
            source, doc, detail, evidence = context
            chosen = _selected_pools(source, doc, detail, evidence, bundle=self,
                allow_draft=self.docstatus == 0, validate_valuation=True, allow_native_pending=True)
            # Keep native FIFO queues, accounting and persistence. Verify its
            # actual per-lot results, including when zero is the original rate.
            # An exception rolls back any native db_set in this transaction.
            _selected_value(evidence, chosen, evidence.get("prior_return_usage", MaterialReturnUsage()), posted=True)
            if serial_row_valuation and save:
                for entry in self.entries:
                    entry.db_set({"incoming_rate": entry.incoming_rate,
                                  "stock_value_difference": entry.stock_value_difference})
        return result

    def check_if_latest(self):
        # A native bundle editor can save independently of its Stock Entry.
        # Serialize that path against the original issue before the native
        # bundle parent/child locks; never detach an existing return bundle.
        from jifeng_erp.material_issue_locking import _lock_issue_sources
        previous_scope = None
        if not self.is_new():
            previous_scope = frappe.db.get_value(BUNDLE, self.name,
                ["voucher_type", "voucher_no", "voucher_detail_no"], as_dict=True)
        scopes = [self, previous_scope] if previous_scope else [self]
        sources = set()
        return_scopes = set()
        for scope in scopes:
            if scope.get("voucher_type") == "Stock Entry" and scope.get("voucher_no"):
                source = frappe.db.get_value("Stock Entry", scope.get("voucher_no"), SOURCE_FIELD)
                if source:
                    sources.add(source)
                    return_scopes.add((scope.get("voucher_type"), scope.get("voucher_no"), scope.get("voucher_detail_no")))
        flows = set()
        for source in sources:
            flows.update(_persisted_flow_ids(source))
        _lock_flows(flows)
        _lock_issue_sources(sources)
        result = super().check_if_latest()
        previous = self.get_doc_before_save() if not self.is_new() else None
        if previous and (previous.get("voucher_type"), previous.get("voucher_no"), previous.get("voucher_detail_no")) in return_scopes:
            if any((previous.get(field) or "") != (self.get(field) or "") for field in
                   ("voucher_type", "voucher_no", "voucher_detail_no", "company", "item_code", "warehouse", "type_of_transaction")):
                _throw("原领料退库批次凭证的原单、原行、物料和原仓库不可改绑。")
        for scope in (self, previous):
            if scope and scope.get("voucher_type") == "Stock Entry" and scope.get("voucher_no"):
                source = frappe.db.get_value("Stock Entry", scope.get("voucher_no"), SOURCE_FIELD)
                if source and source not in sources:
                    _throw("批次凭证的退库来源已变化，请刷新后核对。")
        return result

    def get_return_against(self, parent=None):
        context = _material_bundle_context(self, parent)
        if context:
            return context[0].name
        return super().get_return_against(parent=parent)

    def get_valuation_rate_for_return_entry(self, return_against):
        context = _material_bundle_context(self)
        if not context:
            return super().get_valuation_rate_for_return_entry(return_against)
        source, _, _, evidence = context
        if return_against != source.name:
            _throw("退库批次估值与原领料来源不一致。")
        serials, batches = {}, {}
        for key, pool in evidence["pools"].items():
            rate = float(pool["value"] / pool["qty"])
            if key[0] == "serial":
                serials[key[1]] = rate
                # Native validates batch membership even on serial rows; its
                # serial branch remains the only price applied to those rows.
                if pool["batch"]:
                    batches.setdefault(pool["batch"], 0.0)
            else:
                batches[key[1]] = rate
        return {"serial_nos": serials, "batches": batches}

    def allow_existing_serial_nos(self):
        if self.get("voucher_type") == "Stock Entry" and self.get("type_of_transaction") == "Inward":
            context = _material_bundle_context(self)
            if context:
                # A verified return of its exact issued serial is not a new
                # manufacturing receipt. Native inventory/duplicate checks in
                # validate/on_submit still run; no Stock Settings are changed.
                return
        return super().allow_existing_serial_nos()


def assert_no_material_returns(source_name):
    """Cancellation guard: remove/dismiss all linked returns before the issue."""
    query = {SOURCE_FIELD: source_name, "docstatus": ["<", 2]}
    try:
        current = {row[0] for row in frappe.db.sql("""SELECT name FROM `tabStock Entry`
            WHERE jf_material_return_against=%s AND docstatus<2
            ORDER BY name LIMIT %s FOR UPDATE NOWAIT""", (source_name, MAX_RELATED + 1))}
        raw = frappe.get_all("Stock Entry", filters=query, fields=["name"], limit_page_length=MAX_RELATED + 1)
        visible = frappe.get_list("Stock Entry", filters=query, fields=["name"], limit_page_length=MAX_RELATED + 1)
    except (frappe.PermissionError, frappe.ValidationError):
        frappe.throw("无法核对原领料的退库关联，不能取消原领料。", frappe.PermissionError)
    if {row.name for row in raw} != {row.name for row in visible} or len(raw) > MAX_RELATED:
        frappe.throw("无法完整核对原领料的退库关联，不能取消原领料。", frappe.PermissionError)
    if current != {row.name for row in raw}:
        _throw("原领料的退库关联已变化，请刷新后重新核对，不能取消。")
    if current:
        frappe.throw("原领料仍有退库草稿或已提交退库，请先删除草稿并取消已提交退库。",
                     frappe.ValidationError)
