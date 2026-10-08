"""Create source-bound, editable returns for posted Material Issue vouchers.

Only ordinary Material Issue rows with a complete, unambiguous native stock
ledger valuation are supported. Return drafts use the native Material Receipt
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
MAX_RELATED = 1000
TOLERANCE = Decimal("0.000001")
_SOURCE_ERROR = "原领料来源、公司、仓库或库存账无法完整核对，不能生成领料退库。"


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
    if parenttype:
        permitted = set(meta.get_permitted_fieldnames(
            parenttype=parenttype, permission_type=permission))
    else:
        permitted = set(meta.get_permitted_fieldnames(permission_type=permission))
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


def _source(source_name, *, lock=True):
    if lock:
        _lock_source(source_name)
    try:
        doc = frappe.get_doc("Stock Entry", source_name, for_update=lock)
    except frappe.DoesNotExistError:
        _throw()
    doc.check_permission("read")
    _permitted_fields(doc, ("company", "stock_entry_type", "purpose", "docstatus", "is_return",
                            FLOW_FIELD, "posting_date"))
    _permitted_table(doc, "items", "Stock Entry Detail", (
        "name", "item_code", "item_name", "qty", "uom", "stock_uom", "conversion_factor", "transfer_qty",
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
                or warehouse.is_group or warehouse.disabled
                or item.has_serial_no or item.has_batch_no or row.get("batch_no") or row.get("serial_no")
                or row.get("serial_and_batch_bundle") or row.get("use_serial_batch_fields")):
            _throw("只支持非批次/序列号物料的单一来源仓库领料行。")
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
                            "flow": row.get(FLOW_ROW_FIELD) or source.get(FLOW_FIELD) or ""}
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
        if lock:
            frappe.db.sql("""SELECT name FROM `tabStock Entry`
                WHERE jf_material_return_against=%s AND docstatus<2
                ORDER BY name FOR UPDATE NOWAIT""", (source.name,))
        raw = frappe.get_all("Stock Entry", filters=query, fields=["name"], limit_page_length=MAX_RELATED + 1)
        visible = frappe.get_list("Stock Entry", filters=query, fields=["name"], limit_page_length=MAX_RELATED + 1)
    except (frappe.QueryTimeoutError, frappe.QueryDeadlockError):
        frappe.throw("相关退库凭证正在被处理，请稍后刷新再试", frappe.ValidationError)
    raw_names, visible_names = {row.name for row in raw}, {row.name for row in visible}
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
            "set_basic_rate_manually", "basic_rate",
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


def _source_rates(source, source_rows):
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
        try:
            _permitted_query_fields(SLE, fields)
            raw = frappe.get_all("Stock Ledger Entry", filters=filters, fields=fields,
                                 limit_page_length=MAX_RELATED + 1)
            visible = frappe.get_list("Stock Ledger Entry", filters=filters, fields=fields,
                                     limit_page_length=MAX_RELATED + 1)
        except (frappe.PermissionError, frappe.ValidationError):
            frappe.throw("没有完整读取原领料库存账权限，不能确定退库价值", frappe.PermissionError)
        if (len(raw) > MAX_RELATED or {row.name for row in raw} != {row.name for row in visible}
                or not visible):
            frappe.throw("原领料没有完整可读的库存价值来源，不能生成退库单", frappe.ValidationError)
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
    return rates


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
            FLOW_ROW_FIELD, SOURCE_ROW_FIELD,
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
    _lock_source(source_name)
    source = _source(source_name, lock=False)
    doc.check_permission("create" if doc.is_new() else "write")
    _permitted_fields(doc, ("company", "stock_entry_type", "purpose", SOURCE_FIELD, FLOW_FIELD), "write")
    _permitted_table(doc, "items", "Stock Entry Detail", (
        "item_code", "qty", "uom", "stock_uom", "conversion_factor", "transfer_qty",
        "s_warehouse", "t_warehouse", "basic_rate", "set_basic_rate_manually", "expense_account",
        "cost_center", "project", FLOW_ROW_FIELD, SOURCE_ROW_FIELD, "additional_cost",
        "landed_cost_voucher_amount",
    ), "write")
    if (doc.company != source.company or doc.stock_entry_type != "Material Receipt"
            or doc.purpose != "Material Receipt" or doc.get("is_return") or not doc.items):
        _throw("领料退库必须为同公司Material Receipt并有明细。")
    _no_additional_costs(doc)
    source_rows = _source_rows(source)
    rates = _source_rates(source, source_rows)
    related = _related(source, lock=True)
    if len([value for value in related.values() if value.docstatus == 0 and value.name != doc.name]) > 0:
        frappe.throw("原领料已有另一张退库草稿，请先核对或删除后再保存。", frappe.ValidationError)
    previous = doc.get_doc_before_save() if not doc.is_new() else None
    if previous and previous.get(SOURCE_FIELD) != source_name:
        frappe.throw("退库草稿的原领料来源不可更改。", frappe.ValidationError)
    submitted = _submitted_by_detail(source, source_rows, related)
    current = defaultdict(Decimal)
    seen = set()
    for row in doc.items:
        detail_name = row.get(SOURCE_ROW_FIELD)
        evidence = source_rows.get(detail_name)
        if not evidence or detail_name in seen:
            _throw("每条退库明细必须唯一关联一条原领料明细。")
        seen.add(detail_name)
        _validate_return_row(source, doc, row, evidence, rates[detail_name])
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
    rates = _source_rates(source, source_rows)
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
            "set_basic_rate_manually": 1, "basic_rate": str(rates[detail_name]),
        }
        items.append(row)
    if not items:
        frappe.throw("原领料明细均已退回，没有可生成的剩余数量。", frappe.ValidationError)
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
    _permitted_table(draft, "items", "Stock Entry Detail", tuple(items[0].keys()), "write")
    draft.insert()
    validate_material_return(draft)
    return {"name": draft.name, "docstatus": 0, "reused": False}


def assert_no_material_returns(source_name):
    """Cancellation guard: remove/dismiss all linked returns before the issue."""
    query = {SOURCE_FIELD: source_name, "docstatus": ["<", 2]}
    try:
        raw = frappe.get_all("Stock Entry", filters=query, fields=["name"], limit_page_length=MAX_RELATED + 1)
        visible = frappe.get_list("Stock Entry", filters=query, fields=["name"], limit_page_length=MAX_RELATED + 1)
    except (frappe.PermissionError, frappe.ValidationError):
        frappe.throw("无法核对原领料的退库关联，不能取消原领料。", frappe.PermissionError)
    if {row.name for row in raw} != {row.name for row in visible} or len(raw) > MAX_RELATED:
        frappe.throw("无法完整核对原领料的退库关联，不能取消原领料。", frappe.PermissionError)
    if raw:
        frappe.throw("原领料仍有退库草稿或已提交退库，请先删除草稿并取消已提交退库。",
                     frappe.ValidationError)
