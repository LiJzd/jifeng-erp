"""Serial rollback-only native procurement source acceptance.

Invoke ``smoke`` once through direct Python in an exclusive business-write
window. Never use ``bench execute`` (its fallback may invoke a failed function
again). No commits, permission grants, ignored permissions, ledger edits,
enqueue calls or background workers are used. Stock reposts use the installed
synchronous in_test path. This experiment does not verify a legacy GUI.

Every fixture is saved/submitted/cancelled through its native controller. Only
nested negative gates alter owned source rows or restrict a required field;
their savepoints and exact SQL/callback/context audits run on failure as well.
"""

from collections import Counter
from datetime import date, datetime
from decimal import Decimal
import json

import frappe
from frappe.utils import add_days, today

from jifeng_erp.catalog_validation import _profile_audit, _profile_restore_caller, _profile_rollback
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.fulfillment_validation import _stamp
from jifeng_erp.inventory_validation import _finish_fixture_reposts, _ledger_snapshot, _stock
from jifeng_erp.personnel_changes_validation import _callbacks, _tab_counts
from jifeng_erp.personnel_separation_validation import _local_context
from jifeng_erp.product_dictionary_validation import _native_contents, _sql_row_hash
from jifeng_erp.procurement_fulfillment_workbench import (
    EXTRAS, FLOW, JE, PARENTS, PE, PI, PLE, PO, PR, SCO, SCR, TABLES,
    query_procurement_sources, read_procurement_source,
)


ROLES = {
    "writer": ("Purchase User", "Stock Manager", "Accounts Manager", "Manufacturing User"),
    "reader": ("Purchase User", "Stock User", "Accounts User", "Manufacturing User"),
    "no_flow": ("Purchase User", "Stock User", "Accounts User"),
    "other": ("Purchase User", "Stock User", "Accounts User", "Manufacturing User"),
}
NUMERIC = {
    "docstatus", "idx", "qty", "stock_qty", "conversion_factor", "rate", "base_rate", "amount", "base_amount",
    "net_amount", "base_net_amount", "grand_total", "base_grand_total", "net_total", "base_net_total",
    "rounded_total", "base_rounded_total", "conversion_rate", "per_received", "per_billed", "is_return",
    "is_subcontracted", "is_old_subcontracting_flow", "update_stock", "outstanding_amount", "received_qty",
    "rejected_qty", "returned_qty", "billed_amt", "fg_item_qty", "total_qty", "total", "process_loss_qty",
    "service_cost_per_qty", "rm_cost_per_qty", "additional_cost_per_qty", "subcontracting_conversion_factor",
    "paid_amount", "received_amount", "base_paid_amount", "base_received_amount", "source_exchange_rate",
    "target_exchange_rate", "amount_in_account_currency", "delinked", "multi_currency", "source_changed",
    "allocated_amount", "exchange_rate", "debit_in_account_currency", "credit_in_account_currency",
}
RELATIONS = {PR: "purchase_receipts", PI: "purchase_invoices", SCO: "subcontracting_orders", SCR: "subcontracting_receipts"}


def _number(value):
    result = Decimal(str(value))
    assert result.is_finite(), "来源数量和金额必须是有限原生值"
    return result


def _assert_projection(projected, native, required):
    """Compare every returned field to a freshly reloaded native source."""
    assert set(required).issubset(projected), "来源投影遗漏必需字段"
    for field, value in projected.items():
        original = native.get(field)
        if field in NUMERIC and value is not None and original is not None:
            assert _number(value) == _number(original), (native.get("name"), field, value, original)
        else:
            original = original.isoformat() if isinstance(original, (date, datetime)) else str(original) if isinstance(original, Decimal) else original
            assert value == original, (native.get("name"), field, value, original)


def _assert_related(result, doctype, documents, company_currency=None):
    actual = {part["document"]["name"]: part for part in result[RELATIONS[doctype]]}
    expected = {document.name: document for document in documents}
    assert len(actual) == len(result[RELATIONS[doctype]]) and len(expected) == len(documents) and set(actual) == set(expected), "来源父单遗漏、重复或混入"
    for name, document in expected.items():
        part = actual[name]
        _assert_projection(part["document"], document, PARENTS[doctype][0])
        assert part["amount_scope"] == "whole_voucher" and part["amounts_allocated_to_items"] is False
        assert part["valuation_currency"] == (company_currency if doctype == SCR else document.get("currency", document.get("supplier_currency")))
        assert part["other_items"] == [] and part["unlinked_items"] == [], "隔离实验单据不能混入别单或无来源行"
        rows = {row["name"]: row for row in part["items"]}
        assert len(rows) == len(part["items"]) and set(rows) == {row.name for row in document.items}, "来源子行遗漏、重复或混入"
        for row in document.items:
            assert (row.parent, row.parenttype, row.parentfield) == (name, doctype, "items")
            _assert_projection(rows[row.name], row, TABLES[doctype][2])
    return actual


def _assert_native_purchase_sources(result, order, receipts, invoices):
    """Oracle from saved exact PO/PR/PI identities, not item-code grouping."""
    assert result["readonly"] is True and result["can_write"] is False
    assert result["quantities_aggregated"] is False and result["amounts_allocated_to_items"] is False
    assert (result["company"], result["supplier"]) == (order.company, order.supplier)
    _assert_projection(result["purchase_order"], order, PARENTS[PO][0])
    details = {row.name: row for row in order.items}
    projected = {row["name"]: row for row in result["items"]}
    assert len(projected) == len(result["items"]) and set(projected) == set(details)
    for name, row in details.items():
        assert (row.parent, row.parenttype, row.parentfield) == (order.name, PO, "items")
        _assert_projection(projected[name], row, TABLES[PO][2])
    receipt_rows = {(document.name, row.name): row for document in receipts for row in document.items}
    invoice_rows = {(document.name, row.name): row for document in invoices for row in document.items}
    for doctype, documents in ((PR, receipts), (PI, invoices)):
        _assert_related(result, doctype, documents)
        for document in documents:
            assert document.docstatus in (1, 2) and (document.company, document.supplier) == (order.company, order.supplier)
            for row in document.items:
                child = row.get("purchase_order_item" if doctype == PR else "po_detail")
                assert row.purchase_order == order.name and child in details, "准确采购来源必须是登记的采购子行"
                original = details[child]
                assert (row.item_code, row.jf_flow_order) == (original.item_code, original.jf_flow_order), "同物料两个流程来源串行"
                if doctype == PI and (row.purchase_receipt or row.pr_detail):
                    source = receipt_rows.get((row.purchase_receipt, row.pr_detail))
                    assert source is not None and source.purchase_order_item == child and source.item_code == row.item_code
                source_field = "purchase_receipt_item" if doctype == PR else "purchase_invoice_item"
                if document.is_return:
                    source = (receipt_rows if doctype == PR else invoice_rows).get((document.return_against, row.get(source_field)))
                    assert source is not None and source.item_code == row.item_code and source.jf_flow_order == row.jf_flow_order
                    assert source.get("purchase_order_item" if doctype == PR else "po_detail") == child
                    assert _number(row.qty) < 0 and _number(source.qty) > 0, "退货必须保留准确原行及原生负数量"
                else:
                    assert not document.return_against and not row.get(source_field) and _number(row.qty) > 0


def _assert_native_receipt_ledger(receipts, ledger, company, warehouse):
    """Exact stock voucher/detail movements, including cancelled inverse rows."""
    def token(kind, name, detail, item, qty):
        return kind, name, detail, item, warehouse, company, _number(qty)
    expected = Counter()
    for document in receipts:
        assert document.docstatus in (1, 2)
        for row in document.items:
            assert row.warehouse == warehouse
            expected[token(PR, document.name, row.name, row.item_code, row.stock_qty)] += 1
            if document.docstatus == 2:
                expected[token(PR, document.name, row.name, row.item_code, -_number(row.stock_qty))] += 1
    names = {document.name: document for document in receipts}
    actual = Counter()
    for row in ledger:
        assert row.voucher_type == PR and row.voucher_no in names
        assert int(row.is_cancelled) == int(names[row.voucher_no].docstatus == 2)
        actual[(row.voucher_type, row.voucher_no, row.voucher_detail_no, row.item_code,
                row.warehouse, row.company, _number(row.actual_qty))] += 1
    assert actual == expected, "原生采购库存账必须保留每个准确子行、方向和取消历史"


def _assert_native_payables(result, documents, ledger):
    """Native whole-voucher state and complete supplier PLE, never row shares."""
    part = result["payables"]
    assert (part["amount_scope"], part["balance_basis"], part["amounts_allocated_to_items"]) == (
        "whole_voucher", "native_current_outstanding", False)
    assert part["other_reference_sources"] == [], "本实验没有登记其他往来方的共用付款凭证"
    expected = {(document.doctype, document.name): document for document in documents}
    actual = {(row["doctype"], row["document"]["name"]): row for row in part["voucher_sources"]}
    assert len(actual) == len(part["voucher_sources"]) and len(expected) == len(documents) and set(actual) == set(expected), "应付来源必须包括准确整张原生凭证"
    for key, document in expected.items():
        _assert_projection(actual[key]["document"], document, PARENTS[document.doctype][0])
        field, _, required, _ = TABLES[document.doctype]
        children = document.get(field)
        rows = {row["name"]: row for row in actual[key][field]}
        assert len(rows) == len(actual[key][field]) and set(rows) == {row.name for row in children}
        for row in children:
            _assert_projection(rows[row.name], row, required)
    projected = {row["name"]: row for row in part["ledger_entries"]}
    native = {row.name: row for row in ledger}
    assert len(projected) == len(part["ledger_entries"]) and len(native) == len(ledger) and set(projected) == set(native), "应付账簿遗漏、重复或混入"
    for name, row in native.items():
        assert (row.voucher_type, row.voucher_no) in expected and (row.against_voucher_type, row.against_voucher_no) in expected
        assert (row.company, row.party_type, row.party) == (result["company"], "Supplier", result["supplier"])
        _assert_projection(projected[name], row, PARENTS[PLE][0])


def _assert_native_settlement_ledger(document, ledger, cash, payable, supplier, amount, currency):
    """Submitted base-currency PE/JE must post their exact AP debit/cash credit."""
    assert document.docstatus == 1 and document.doctype in (PE, JE)
    amount = _number(amount)
    expected = Counter({(payable, "Supplier", supplier, amount, Decimal(0), amount, Decimal(0)): 1,
                        (cash, None, None, Decimal(0), amount, Decimal(0), amount): 1})
    actual = Counter()
    for row in ledger:
        assert (row.voucher_type, row.voucher_no, row.company, row.account_currency) == (
            document.doctype, document.name, document.company, currency)
        assert not row.is_cancelled
        actual[(row.account, row.party_type or None, row.party or None, _number(row.debit), _number(row.credit),
                _number(row.debit_in_account_currency), _number(row.credit_in_account_currency))] += 1
    assert actual == expected, "已提交原生付款/日记账必须保留准确整张应付借方和现金贷方"


def _assert_native_subcontract_sources(result, purchase, subcontract, receipt, service_receipt, invoice):
    """Service PO row -> different FG row -> service PR/PI exact conversion."""
    assert result["readonly"] is True and result["can_write"] is False and result["quantities_aggregated"] is False and result["amounts_allocated_to_items"] is False
    assert (result["company"], result["supplier"]) == (purchase.company, purchase.supplier)
    _assert_projection(result["purchase_order"], purchase, PARENTS[PO][0])
    assert len(purchase.items) == len(subcontract.items) == len(subcontract.service_items) == len(receipt.items) == len(service_receipt.items) == len(invoice.items) == 1
    po, fg, service, received, purchased, billed = (purchase.items[0], subcontract.items[0],
        subcontract.service_items[0], receipt.items[0], service_receipt.items[0], invoice.items[0])
    assert len(result["items"]) == 1 and result["items"][0]["name"] == po.name
    _assert_projection(result["items"][0], po, TABLES[PO][2])
    assert (service.parent, service.parenttype, service.parentfield) == (subcontract.name, SCO, "service_items")
    assert purchase.is_subcontracted and not purchase.is_old_subcontracting_flow
    assert all(document.docstatus == 1 and (document.company, document.supplier) == (purchase.company, purchase.supplier)
               for document in (subcontract, receipt, service_receipt, invoice))
    assert subcontract.purchase_order == purchase.name
    assert po.item_code == service.item_code == purchased.item_code == billed.item_code and po.fg_item == fg.item_code == received.item_code
    assert po.item_code != po.fg_item and not po.jf_flow_order and fg.jf_flow_order == received.jf_flow_order
    assert service.purchase_order_item == fg.purchase_order_item == received.purchase_order_item == purchased.purchase_order_item == billed.po_detail == po.name
    assert (received.subcontracting_order, received.subcontracting_order_item, received.purchase_order) == (subcontract.name, fg.name, purchase.name)
    assert (service_receipt.subcontracting_receipt, purchased.subcontracting_receipt_item, purchased.purchase_order) == (receipt.name, received.name, purchase.name)
    assert (billed.purchase_receipt, billed.pr_detail, billed.purchase_order) == (service_receipt.name, purchased.name, purchase.name)
    ratio = _number(po.qty) / _number(po.fg_item_qty)
    assert _number(service.qty) == _number(fg.qty) * ratio and _number(purchased.qty) == _number(received.qty) * ratio and _number(billed.qty) == _number(purchased.qty)
    assert not purchased.jf_flow_order and not billed.jf_flow_order, "服务商品不能冒充成品流程来源"
    for doctype, documents in ((SCO, [subcontract]), (SCR, [receipt]), (PR, [service_receipt]), (PI, [invoice])):
        related = _assert_related(result, doctype, documents, result["company_currency"])
        if doctype == SCO:
            rows = related[subcontract.name]["service_items"]
            assert len(rows) == 1 and rows[0]["name"] == service.name
            _assert_projection(rows[0], service, EXTRAS[SCO][2])


def _contents():
    result = _native_contents()
    doctypes = set(PARENTS) | {value[1] for value in TABLES.values()} | {value[1] for value in EXTRAS.values()} | {
        "Company", "Warehouse", "Price List", "Supplier Group", "Bin", "Stock Entry", "BOM",
        "Repost Item Valuation", "Repost Payment Ledger", "Repost Accounting Ledger", "Stock Ledger Entry", "GL Entry",
        "DocType", "DocField", "DocPerm", "Custom DocPerm", "Custom Field", "Property Setter", "Error Log",
        "Communication", "Email Queue", "Notification Log", "Activity Log", "Stock Reservation Entry",
        "Role", "User Type", "User Group", "Global Defaults",
    }
    doctypes = {doctype for doctype in doctypes if frappe.db.exists("DocType", doctype)}
    for doctype in tuple(doctypes):
        doctypes.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    result.update({doctype: _sql_row_hash(frappe.db.sql("SELECT * FROM `tab" + doctype.replace("`", "``")
        + "` ORDER BY name", as_dict=True)) for doctype in sorted(doctypes)})
    result["Singles"] = _sql_row_hash(frappe.db.sql("SELECT * FROM `tabSingles` ORDER BY doctype, field", as_dict=True))
    if frappe.db.table_exists("DefaultValue"):
        result["DefaultValue"] = _sql_row_hash(frappe.db.sql("SELECT * FROM `tabDefaultValue` ORDER BY name", as_dict=True))
    return result


def _preflight(company, other_company, item_group, stock_uom, include_subcontract):
    assert frappe.db.db_type == "mariadb" and other_company and other_company != company
    for doctype, name in (("Company", company), ("Company", other_company), ("Item Group", item_group), ("UOM", stock_uom)):
        assert frappe.db.exists(doctype, name), "采购履行验收需要既有两公司、商品分组和库存单位"
    assert not frappe.db.get_value("Item Group", item_group, "is_group")
    assert not frappe.flags.dont_execute_stock_reposts and not frappe.flags.through_repost_item_valuation
    assert frappe.db.get_single_value("Buying Settings", "pr_required") != "Yes", "直接 PO 发票验收需要站点本身允许无 PR 发票；不修改采购控制设置"
    assert not frappe.db.exists("Property Setter", {"doc_type": TABLES[PR][1], "field_name": "purchase_order_item", "property": "permlevel"}), "必需子字段权限门需要没有既有 permlevel Property Setter；不覆盖站点现有权限设置"
    if include_subcontract:
        assert not frappe.db.get_single_value("Buying Settings", "auto_create_subcontracting_order")
        assert not frappe.db.get_single_value("Buying Settings", "auto_create_purchase_receipt"), "显式逐单验收需要关闭自动生成委外采购收货"
    for doctype in (PO, PR, PI, SCO, SCR):
        field = frappe.get_meta(TABLES[doctype][1]).get_field("jf_flow_order")
        assert field and field.options == FLOW
    company_doc = frappe.get_doc("Company", company)
    values = {}
    for label, field, condition in (
        ("payable", "default_payable_account", {"account_type": "Payable"}),
        ("expense", "default_expense_account", {"account_type": "Cost of Goods Sold"}),
        ("cash", "default_cash_account", {"account_type": "Cash"}),
    ):
        values[label] = company_doc.get(field) or frappe.db.get_value("Account", {"company": company, "is_group": 0, **condition}, "name")
    values["cost_center"] = company_doc.cost_center or frappe.db.get_value("Cost Center", {"company": company, "is_group": 0}, "name")
    values["supplier_group"] = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
    values["currency"] = company_doc.default_currency
    assert all(values.values()), "原生采购验收需要本币、应付/成本/现金科目、明细成本中心与供应商分组"
    for key in ("payable", "expense", "cash"):
        account = frappe.get_doc("Account", values[key])
        assert not account.is_group and account.company == company and (account.account_currency or values["currency"]) == values["currency"], "本实验只使用本公司的本币明细科目"
    return frappe._dict(company=company, **values)


def smoke(company, other_company, item_group, stock_uom, include_subcontract=True):
    """Real native purchasing/payment/stock story; all changes roll back."""
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
    from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import make_debit_note
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_invoice, make_purchase_receipt, make_subcontracting_order
    from erpnext.controllers.subcontracting_controller import make_rm_stock_entry
    from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice as invoice_from_receipt, make_purchase_return
    from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import get_mapped_subcontracting_receipt
    from erpnext.subcontracting.doctype.subcontracting_receipt.subcontracting_receipt import make_purchase_receipt as service_receipt_from_subcontract

    frappe.only_for("System Manager")
    fixture = _preflight(company, other_company, item_group, stock_uom, include_subcontract)
    batch_date = next((add_days(today(), -offset) for offset in range(1, 367)
        if not frappe.db.exists(PO, {"company": company, "transaction_date": add_days(today(), -offset)})), None)
    assert batch_date, "隔离采购批次需要一年内没有既有 PO 的日期"
    tables, counts = _tab_counts()
    before, ledger_before, callbacks, context = _contents(), _ledger_snapshot(), _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    original_repost, original_max_writes = frappe.flags.through_repost_item_valuation, frappe.db.MAX_WRITES_PER_TRANSACTION
    prefix, point = "JF-QA-PROC-FOLLOW-" + frappe.generate_hash(length=10), "jf_proc_follow_" + frappe.generate_hash(length=8)
    objects, records, users, actors, roles, vouchers, checks, items, warehouses = [], set(), [], {}, {}, set(), [], set(), set()
    current, primary = "fixture_setup", None

    def remember(document):
        objects.append(document)
        if document.name:
            records.add((document.doctype, document.name))
        return document

    def insert(doctype, **values):
        return remember(frappe.get_doc({"doctype": doctype, **values})).insert()

    def native(document, action):
        old_repost, old_writes = frappe.flags.through_repost_item_valuation, frappe.db.MAX_WRITES_PER_TRANSACTION
        try:
            return getattr(document, action)()
        finally:
            frappe.flags.through_repost_item_valuation, frappe.db.MAX_WRITES_PER_TRANSACTION = old_repost, old_writes

    def post(document):
        document = remember(frappe.get_doc(document.as_dict()))
        native(document, "insert")
        records.add((document.doctype, document.name))
        native(document, "submit")
        document.reload()
        vouchers.add((document.doctype, document.name))
        return document

    def completed_reposts():
        _finish_fixture_reposts(company, items, warehouses, vouchers)

    def read(action=None):
        state, queued, local = _contents(), _callbacks(), _local_context()
        result = (action or (lambda: read_procurement_source(company, order.name)))()
        assert _contents() == state and _callbacks() == queued and _local_context() == local, "只读采购来源查询改变原生 SQL/回调/上下文"
        return result

    def rejected(action, hidden=(), errors=frappe.PermissionError):
        state, queued, local = _contents(), _callbacks(), _local_context()
        try:
            action()
        except errors as error:
            assert all(name not in str(error) for name in hidden), "错误泄露隐藏来源标识"
        else:
            raise AssertionError("完整采购来源查询接受了应拒绝的来源")
        assert _contents() == state and _callbacks() == queued and _local_context() == local

    def nested_gate(label, setup, action=None, errors=frappe.PermissionError, hidden=(), doctypes=()):
        nonlocal current
        current = label
        saved, saved_callbacks, saved_context = _contents(), _callbacks(), _local_context()
        nested, nested_primary = "jf_proc_gate_" + frappe.generate_hash(length=8), None
        _begin_rollback_only(nested)
        try:
            frappe.set_user("Administrator")
            setup()
            frappe.set_user(actors["reader"])
            if errors is None:
                read(action)
            else:
                rejected(action or (lambda: read_procurement_source(company, order.name)), hidden, errors)
        except Exception as error:
            nested_primary = error
            raise
        finally:
            def restore_nested():
                try:
                    for doctype in doctypes:
                        frappe.clear_cache(doctype=doctype)
                finally:
                    _profile_restore_caller(actors["reader"], frappe.in_test, [actors["reader"]], records, saved_callbacks, saved_context)
            _profile_audit([
                ("nested_database_and_callbacks_rollback", lambda: _profile_rollback(nested, saved_callbacks)),
                ("nested_meta_owned_caches_actor_context_restore", restore_nested),
                ("nested_complete_sql_restored", lambda: _contents() == saved),
                ("nested_callbacks_context_restored", lambda: _callbacks() == saved_callbacks and _local_context() == saved_context),
            ], primary=nested_primary)
        checks.append(label)

    def restriction(doctype, name):
        insert("User Permission", user=actors["reader"], allow=doctype, for_value=name,
               apply_to_all_doctypes=0, applicable_for=doctype)
        frappe.clear_cache(user=actors["reader"])

    def fresh(document):
        return frappe.get_doc(document.doctype, document.name)

    def native_ple(supplier):
        return frappe.get_all(PLE, filters={"company": company, "party_type": "Supplier", "party": supplier}, fields=["*"], order_by="name asc", limit_page_length=0)

    def receipt_ledger(receipts):
        return frappe.get_all("Stock Ledger Entry", filters={"voucher_type": PR, "voucher_no": ["in", [doc.name for doc in receipts]]}, fields=["*"], order_by="name asc", limit_page_length=0)

    def make_item(label, stock=True, subcontracted=False):
        document = insert("Item", item_code=prefix + "-" + label, item_group=item_group, stock_uom=stock_uom,
            is_stock_item=int(stock), is_sub_contracted_item=int(subcontracted), valuation_method="Moving Average",
            item_defaults=[{"company": company, "default_warehouse": fixture.warehouse.name,
                "expense_account": fixture.expense, "buying_cost_center": fixture.cost_center}])
        items.add(document.name)
        return document

    def purchase(supplier, rows, subcontracted=False):
        return post(frappe.get_doc({"doctype": PO, "company": company, "supplier": supplier.name,
            "transaction_date": batch_date, "schedule_date": today(), "currency": fixture.currency,
            "conversion_rate": 1, "buying_price_list": fixture.price_list.name, "price_list_currency": fixture.currency,
            "plc_conversion_rate": 1, "ignore_pricing_rule": 1, "disable_rounded_total": 1,
            "is_subcontracted": int(subcontracted), "is_old_subcontracting_flow": 0,
            "set_warehouse": fixture.warehouse.name, "items": rows}))

    _begin_rollback_only(point)
    try:
        frappe.in_test = True
        frappe.set_user("Administrator")
        fixture.warehouse = insert("Warehouse", company=company, warehouse_name=prefix)
        warehouses.add(fixture.warehouse.name)
        fixture.item = make_item("normal")
        fixture.suppliers = [insert("Supplier", supplier_name=prefix + "-supplier-" + str(index),
            supplier_type="Company", supplier_group=fixture.supplier_group,
            accounts=[{"company": company, "account": fixture.payable}]) for index in (1, 2)]
        fixture.price_list = insert("Price List", price_list_name=prefix, currency=fixture.currency, buying=1, enabled=1)
        flows = [insert(FLOW, company=company, item_code=fixture.item.name, planned_qty=qty,
            materials=[{"item_code": fixture.item.name, "uom": stock_uom, "qty_per_unit": 1, "unit_price": rate}])
            for qty, rate in ((6, 10), (4, 15))]
        for label, assigned in ROLES.items():
            user = insert("User", email=prefix.lower() + "-" + label + "@example.invalid", first_name="采购履行验收",
                send_welcome_email=0, roles=[{"role": role} for role in assigned])
            users.append(user.name)
            actors[label] = user.name
            insert("User Permission", user=user.name, allow="Company", for_value=other_company if label == "other" else company, apply_to_all_doctypes=1)
            frappe.clear_cache(user=user.name)
            roles[label] = sorted(frappe.get_roles(user.name))
            assert "System Manager" not in roles[label]
        frappe.set_user(actors["writer"])
        order = purchase(fixture.suppliers[0], [{"item_code": fixture.item.name, "qty": qty, "rate": rate,
            "warehouse": fixture.warehouse.name, "schedule_date": today(), "jf_flow_order": flow.name}
            for qty, rate, flow in ((6, 10, flows[0]), (4, 15, flows[1]))])
        first, second = (row.name for row in order.items)
        assert first != second and order.items[0].item_code == order.items[1].item_code
        checks.append("native_same_item_two_po_children_and_distinct_flows_with_real_roles")

        current = "native_batches_receipts_and_invoices"
        receipts, invoices = [], []
        for index, (child, qty) in enumerate(((first, 2), (first, 1), (second, 1))):
            document = make_purchase_receipt(order.name, args={"filtered_children": [child]})
            assert len(document.items) == 1 and document.items[0].purchase_order_item == child
            document.items[0].qty, document.items[0].received_qty = qty, qty
            received = post(_stamp(document, f"08:{index * 10:02d}:00"))
            receipts.append(received)
            document = invoice_from_receipt(received.name)
            document.credit_to, document.update_stock = fixture.payable, 0
            invoices.append(post(_stamp(document, f"09:{index * 10:02d}:00")))
        document = make_purchase_invoice(order.name, args={"filtered_children": [second]})
        assert len(document.items) == 1 and document.items[0].po_detail == second and not document.items[0].pr_detail
        document.items[0].qty, document.credit_to, document.update_stock = 1, fixture.payable, 0
        direct = post(_stamp(document, "10:00:00"))
        invoices.append(direct)
        completed_reposts()
        frappe.set_user(actors["reader"])
        result = read()
        _assert_native_purchase_sources(result, fresh(order), [fresh(doc) for doc in receipts], [fresh(doc) for doc in invoices])
        _assert_native_receipt_ledger(receipts, receipt_ledger(receipts), company, fixture.warehouse.name)
        _assert_native_payables(result, [fresh(doc) for doc in [order, *invoices]], native_ple(fixture.suppliers[0].name))
        _stock(fixture.item.name, fixture.warehouse.name, today(), 4, 45)
        assert [row["purchase_order"]["name"] for row in read(lambda: query_procurement_sources(company, batch_date, batch_date, fixture.suppliers[0].name, order.name))] == [order.name]
        checks.append("native_three_pr_pi_batches_and_direct_po_invoice_keep_exact_child_sources_and_stock")

        current = "native_return_credit_and_posted_payment_journal"
        frappe.set_user(actors["writer"])
        document = make_purchase_return(receipts[2].name)
        document.items[0].qty, document.items[0].received_qty = -1, -1
        returned_receipt = post(_stamp(document, "11:00:00"))
        receipts.append(returned_receipt)
        document = make_debit_note(invoices[2].name)
        document.items[0].qty, document.update_stock = -1, 0
        returned_invoice = post(_stamp(document, "11:10:00"))
        invoices.append(returned_invoice)
        payment = get_payment_entry(PI, invoices[0].name, party_amount=3, bank_account=fixture.cash, bank_amount=3)
        payment.posting_date, payment.reference_no, payment.reference_date = today(), prefix + "-payment", today()
        payment = post(payment)
        journal = post(frappe.get_doc({"doctype": JE, "company": company, "posting_date": today(), "voucher_type": "Journal Entry",
            "accounts": [{"account": fixture.payable, "debit_in_account_currency": 2, "party_type": "Supplier",
                "party": fixture.suppliers[0].name, "reference_type": PI, "reference_name": invoices[1].name,
                "cost_center": fixture.cost_center}, {"account": fixture.cash, "credit_in_account_currency": 2,
                "cost_center": fixture.cost_center}]}))
        assert payment.docstatus == journal.docstatus == 1
        assert payment.references[0].reference_name == invoices[0].name and _number(payment.references[0].allocated_amount) == 3
        assert journal.accounts[0].reference_name == invoices[1].name and _number(journal.accounts[0].debit_in_account_currency) == 2
        for document, amount in ((payment, 3), (journal, 2)):
            gl = frappe.get_all("GL Entry", filters={"voucher_type": document.doctype, "voucher_no": document.name}, fields=["*"], order_by="name asc", limit_page_length=0)
            _assert_native_settlement_ledger(document, gl, fixture.cash, fixture.payable, fixture.suppliers[0].name, amount, fixture.currency)
            assert frappe.db.exists(PLE, {"voucher_type": document.doctype, "voucher_no": document.name})
        completed_reposts()
        frappe.set_user(actors["reader"])
        result = read()
        _assert_native_purchase_sources(result, fresh(order), [fresh(doc) for doc in receipts], [fresh(doc) for doc in invoices])
        _assert_native_receipt_ledger(receipts, receipt_ledger(receipts), company, fixture.warehouse.name)
        payment_sources = [order, *invoices, payment, journal]
        _assert_native_payables(result, [fresh(doc) for doc in payment_sources], native_ple(fixture.suppliers[0].name))
        checks.append("native_pr_return_pi_debit_note_posted_pe_je_and_current_whole_invoice_outstanding")

        current = "native_supplier_scope"
        frappe.set_user(actors["writer"])
        other_order = purchase(fixture.suppliers[1], [{"item_code": fixture.item.name, "qty": 2, "rate": 10,
            "warehouse": fixture.warehouse.name, "schedule_date": today()}])
        document = make_purchase_invoice(other_order.name)
        document.credit_to, document.update_stock = fixture.payable, 0
        other_invoice = post(_stamp(document, "12:00:00"))
        other_payment = get_payment_entry(PI, other_invoice.name, party_amount=2, bank_account=fixture.cash, bank_amount=2)
        other_payment.posting_date, other_payment.reference_no, other_payment.reference_date = today(), prefix + "-other", today()
        other_payment = post(other_payment)
        completed_reposts()
        frappe.set_user(actors["reader"])
        second_result = read(lambda: read_procurement_source(company, other_order.name))
        _assert_native_purchase_sources(second_result, fresh(other_order), [], [fresh(other_invoice)])
        _assert_native_payables(second_result, [fresh(doc) for doc in (other_order, other_invoice, other_payment)], native_ple(fixture.suppliers[1].name))
        _assert_native_payables(read(), [fresh(doc) for doc in payment_sources], native_ple(fixture.suppliers[0].name))
        assert [row["purchase_order"]["name"] for row in read(lambda: query_procurement_sources(company, batch_date, batch_date, fixture.suppliers[1].name))] == [other_order.name]
        checks.append("native_two_suppliers_shared_item_payable_account_are_independent_batches_and_ple")
        nested_gate("native_hidden_supplier_denies_root", lambda: restriction("Supplier", fixture.suppliers[0].name),
            action=lambda: read_procurement_source(company, other_order.name), hidden=(fixture.suppliers[1].name,))
        for kind, allowed, hidden in ((PR, receipts[0].name, receipts[1].name), (PI, invoices[0].name, invoices[1].name),
                                     (PE, other_payment.name, payment.name), (FLOW, flows[0].name, flows[1].name)):
            nested_gate("native_hidden_" + kind + "_whole_view_denied", lambda dt=kind, name=allowed: restriction(dt, name), hidden=(hidden,))
        frappe.set_user(actors["writer"])
        control_je = insert(JE, company=company, posting_date=today(), voucher_type="Journal Entry",
            accounts=[{"account": fixture.expense, "debit_in_account_currency": 1, "cost_center": fixture.cost_center},
                      {"account": fixture.cash, "credit_in_account_currency": 1, "cost_center": fixture.cost_center}])
        frappe.set_user(actors["reader"])
        nested_gate("native_hidden_journal_whole_view_denied", lambda: restriction(JE, control_je.name), hidden=(journal.name,))

        def hide_child_field():
            insert("Property Setter", doctype_or_field="DocField", doc_type=TABLES[PR][1], field_name="purchase_order_item",
                   property="permlevel", property_type="Int", value="9")
            frappe.clear_cache(doctype=TABLES[PR][1])
        nested_gate("native_child_mandatory_source_field_hidden_whole_view_denied", hide_child_field, doctypes=(TABLES[PR][1],))
        nested_gate("native_corrupt_exact_po_child_denied", lambda: frappe.db.set_value(TABLES[PR][1], receipts[0].items[0].name,
            "purchase_order_item", second), errors=frappe.ValidationError)
        nested_gate("native_corrupt_supplier_denied", lambda: frappe.db.set_value(PI, direct.name, "supplier", fixture.suppliers[1].name), errors=frappe.ValidationError)
        nested_gate("native_cross_company_source_denied", lambda: frappe.db.set_value(PR, receipts[1].name, "company", other_company),
                    errors=(frappe.PermissionError, frappe.ValidationError))
        frappe.set_user(actors["no_flow"])
        assert not frappe.has_permission(FLOW, "read")
        rejected(lambda: read_procurement_source(company, order.name), hidden=tuple(flow.name for flow in flows))
        frappe.set_user(actors["other"])
        rejected(lambda: read_procurement_source(company, order.name), hidden=(order.name,))
        frappe.set_user("Guest")
        rejected(lambda: read_procurement_source(company, order.name), hidden=(order.name,))
        checks.append("native_real_role_company_guest_restrictions_deny_complete_view")

        if include_subcontract:
            current = "native_service_to_finished_subcontract_chain"
            frappe.set_user("Administrator")
            raw, finished, service = make_item("sub-raw"), make_item("sub-fg", subcontracted=True), make_item("sub-service", stock=False)
            supplier_warehouse = insert("Warehouse", company=company, warehouse_name=prefix + "-supplier")
            warehouses.add(supplier_warehouse.name)
            frappe.set_user(actors["writer"])
            bom = post(frappe.get_doc({"doctype": "BOM", "company": company, "item": finished.name, "quantity": 1,
                "is_active": 1, "is_default": 1, "with_operations": 0, "cost_allocation_per": 100,
                "currency": fixture.currency, "items": [{"item_code": raw.name, "qty": 1, "rate": 5}]}))
            flow = insert(FLOW, company=company, item_code=finished.name, planned_qty=4, bom=bom.name,
                materials=[{"item_code": raw.name, "uom": stock_uom, "qty_per_unit": 1, "unit_price": 5}])
            post(_stamp(frappe.get_doc({"doctype": "Stock Entry", "company": company, "stock_entry_type": "Material Receipt",
                "items": [{"item_code": raw.name, "qty": 4, "basic_rate": 5, "t_warehouse": fixture.warehouse.name,
                    "expense_account": fixture.expense, "cost_center": fixture.cost_center}]}), "13:00:00"))
            sub_purchase = purchase(fixture.suppliers[0], [{"item_code": service.name, "qty": 2, "rate": 20,
                "fg_item": finished.name, "fg_item_qty": 4, "warehouse": fixture.warehouse.name, "schedule_date": today()}], subcontracted=True)
            document = make_subcontracting_order(sub_purchase.name)
            document.supplier_warehouse = supplier_warehouse.name
            document.items[0].bom, document.items[0].jf_flow_order = bom.name, flow.name
            document.items[0].schedule_date = document.items[0].expected_delivery_date = today()
            subcontract = post(document)
            rm = subcontract.supplied_items[0]
            post(_stamp(frappe.get_doc(make_rm_stock_entry(subcontract.name, [{"name": rm.name, "main_item_code": finished.name,
                "rm_item_code": raw.name, "qty": 4, "item_name": raw.name, "stock_uom": stock_uom, "warehouse": fixture.warehouse.name}])), "13:10:00"))
            sub_receipt = post(_stamp(get_mapped_subcontracting_receipt(subcontract.name,
                items=[{"name": subcontract.items[0].name, "qty": 2}]), "13:20:00"))
            service_receipt = post(_stamp(service_receipt_from_subcontract(sub_receipt.name), "13:30:00"))
            document = invoice_from_receipt(service_receipt.name)
            document.credit_to, document.update_stock = fixture.payable, 0
            service_invoice = post(_stamp(document, "13:40:00"))
            control_sub_purchase = purchase(fixture.suppliers[1], [{"item_code": service.name, "qty": 1, "rate": 20,
                "fg_item": finished.name, "fg_item_qty": 2, "warehouse": fixture.warehouse.name, "schedule_date": today()}], subcontracted=True)
            document = make_subcontracting_order(control_sub_purchase.name)
            document.supplier_warehouse = supplier_warehouse.name
            document.items[0].bom = bom.name
            control_subcontract = remember(frappe.get_doc(document.as_dict()))
            native(control_subcontract, "insert")
            records.add((control_subcontract.doctype, control_subcontract.name))
            completed_reposts()
            frappe.set_user(actors["reader"])
            sub_result = read(lambda: read_procurement_source(company, sub_purchase.name))
            _assert_native_subcontract_sources(sub_result, *(fresh(doc) for doc in (sub_purchase, subcontract, sub_receipt, service_receipt, service_invoice)))
            _stock(raw.name, fixture.warehouse.name, today(), 0, 0)
            _stock(raw.name, supplier_warehouse.name, today(), 2, 10)
            _stock(finished.name, fixture.warehouse.name, today(), 2, 30)
            assert not frappe.db.exists("Stock Ledger Entry", {"voucher_type": PR, "voucher_no": service_receipt.name})
            checks.append("native_no_operation_bom_service_neq_fg_sco_scr_to_service_pr_pi_half_conversion_stock")
            nested_gate("native_hidden_subcontract_parent_whole_view_denied", lambda: restriction(SCO, control_subcontract.name),
                action=lambda: read_procurement_source(company, sub_purchase.name), hidden=(subcontract.name,))

        current = "native_cancelled_return_payment_history"
        frappe.set_user(actors["writer"])
        for document in (payment, returned_invoice, returned_receipt):
            native(fresh(document), "cancel")
        completed_reposts()
        frappe.set_user(actors["reader"])
        cancelled = read()
        _assert_native_purchase_sources(cancelled, fresh(order), [fresh(doc) for doc in receipts], [fresh(doc) for doc in invoices])
        _assert_native_receipt_ledger([fresh(doc) for doc in receipts], receipt_ledger(receipts), company, fixture.warehouse.name)
        _stock(fixture.item.name, fixture.warehouse.name, today(), 4, 45)
        # The same supplier's independent SC invoice belongs to a different PO;
        # compare only exact reachable PLE, without treating the supplier as a PO.
        expected_keys = {(doc.doctype, doc.name) for doc in payment_sources}
        own_ledger = [row for row in native_ple(fixture.suppliers[0].name) if
            (row.voucher_type, row.voucher_no) in expected_keys or (row.against_voucher_type, row.against_voucher_no) in expected_keys]
        _assert_native_payables(cancelled, [fresh(doc) for doc in payment_sources], own_ledger)
        checks.append("native_cancelled_pr_pi_pe_history_retains_exact_sources_and_inverse_stock")
    except Exception as error:
        primary = error
        error.add_note("采购履行原生失败门：" + current)
        error.add_note("已通过门：" + json.dumps(checks, ensure_ascii=False))
        raise
    finally:
        def collect():
            for document in objects:
                if document.name:
                    records.add((document.doctype, document.name))
                records.update((row.doctype, row.name) for row in document.get_all_children() if row.name)

        def restore():
            frappe.flags.through_repost_item_valuation, frappe.db.MAX_WRITES_PER_TRANSACTION = original_repost, original_max_writes
            _profile_restore_caller(original_user, original_test, users, records, callbacks, context)

        _profile_audit([
            ("collect_exact_fixture_parents_children", collect),
            ("native_rollback_and_only_added_callbacks", lambda: _profile_rollback(point, callbacks)),
            ("restore_actor_context_flags_and_exact_owned_caches", restore),
            ("all_owned_parent_children_absent", lambda: all(not frappe.db.exists(dt, name) for dt, name in records)),
            ("full_business_children_auth_roles_series_metadata_ledgers_sql_restored", lambda: _contents() == before),
            ("all_tab_table_counts_restored", lambda: _tab_counts(tables)[1] == counts),
            ("whole_gl_sle_restored", lambda: _ledger_snapshot() == ledger_before),
            ("caller_callbacks_and_local_context_restored", lambda: _callbacks() == callbacks and _local_context() == context),
        ], primary=primary)
    checks.append("full_native_sql_auth_series_children_counts_ledgers_context_rollback")
    return {"passed": True, "gate_count": len(checks), "checks": checks, "business_data": "rolled_back",
        "actual_roles": roles, "contents_before": before, "contents_after": _contents(),
        "table_counts_before": counts, "table_counts_after": _tab_counts(tables)[1],
        "gui_verified": False, "concurrency_verified": False, "subcontract_verified": bool(include_subcontract),
        "remaining": ["旧采购履行页面及操作习惯", "跨 PO 共用 PR/PI/PE/JE 的混合来源", "多币种及税费",
            "委外副产品、拒收、损耗和退货", "旧版委外流程", "先 PI 后 PR", "原生字段 mask 专项",
            "真实付款/财务重过账 worker、已提交凭证暂缺 PLE", "Company 仅 select 岗位", "双连接并发来源或权限变化"]}
